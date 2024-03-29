import collections
import json
import logging
import os
import re
import string
import numpy as np
from collections import Counter
from functools import partial
from multiprocessing import Pool, cpu_count
import spacy
import torch
from torch.utils.data import TensorDataset
from tqdm import tqdm
from processors.utils import DataProcessor
import numpy as np

class CoqaExample(object):
    """Single CoQA example"""
    def __init__(
            self,
            qas_id,
            question_text,
            doc_tokens,
            orig_answer_text=None,
            start_position=None,
            end_position=None,
            rational_span=None
            #rational_start_position=None,
            #rational_end_position=None,
            #additional_answers=None,
    ):
        self.qas_id = qas_id
        self.question_text = question_text
        self.doc_tokens = doc_tokens
        self.orig_answer_text = orig_answer_text
        self.start_position = start_position
        self.end_position = end_position
        self.rational_span = rational_span
        #self.additional_answers = additional_answers
        #self.rational_start_position = rational_start_position
        #self.rational_end_position = rational_end_position

class CoqaFeatures(object):
    """Single CoQA feature"""
    def __init__(self,
                 unique_id,
                 example_index,
                 doc_span_index,
                 tokens,
                 token_to_orig_map,
                 token_is_max_context,
                 input_ids,
                 input_mask,
                 segment_ids,
                 start_position=None,
                 end_position=None,
                 cls_idx=None,
                 rational_mask=None):
        self.unique_id = unique_id
        self.example_index = example_index
        self.doc_span_index = doc_span_index
        self.tokens = tokens
        self.token_to_orig_map = token_to_orig_map
        self.token_is_max_context = token_is_max_context
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.start_position = start_position
        self.end_position = end_position
        self.cls_idx = cls_idx
        self.rational_mask = rational_mask

class Result(object):
    def __init__(self, unique_id, start_logits, end_logits, yes_logits, no_logits, unk_logits):
        self.unique_id = unique_id
        self.start_logits = start_logits
        self.end_logits = end_logits
        self.yes_logits = yes_logits
        self.no_logits = no_logits
        self.unk_logits = unk_logits

def _improve_answer_span(doc_tokens, input_start, input_end, tokenizer, orig_answer_text):
    tok_answer_text = " ".join(tokenizer.tokenize(orig_answer_text))
    for new_start in range(input_start, input_end + 1):
        for new_end in range(input_end, new_start - 1, -1):
            text_span = " ".join(doc_tokens[new_start: (new_end + 1)])
            if text_span == tok_answer_text:
                return (new_start, new_end)

    return (input_start, input_end)

def _check_is_max_context(doc_spans, cur_span_index, position):
    best_score = None
    best_span_index = None
    for (span_index, doc_span) in enumerate(doc_spans):
        end = doc_span.start + doc_span.length - 1
        if position < doc_span.start:
            continue
        if position > end:
            continue
        num_left_context = position - doc_span.start
        num_right_context = end - position
        score = min(num_left_context, num_right_context) + 0.01 * doc_span.length
        if best_score is None or score > best_score:
            best_score = score
            best_span_index = span_index

    return cur_span_index == best_span_index

class Processor(DataProcessor):
    train_file = "coqa-train-v1.0.json"
    dev_file = "coqa-dev-v1.0.json"

    def is_whitespace(self, c):
        if c == " " or c == "\t" or c == "\r" or c == "\n" or ord(c) == 0x202F:
            return True
        return False

    def _str(self, s):
        """ Convert PTB tokens to normal tokens """
        if (s.lower() == '-lrb-'):
            s = '('
        elif (s.lower() == '-rrb-'):
            s = ')'
        elif (s.lower() == '-lsb-'):
            s = '['
        elif (s.lower() == '-rsb-'):
            s = ']'
        elif (s.lower() == '-lcb-'):
            s = '{'
        elif (s.lower() == '-rcb-'):
            s = '}'
        return s

    def space_extend(self, matchobj):
        return ' ' + matchobj.group(0) + ' '

    def pre_proc(self, text):
        text = re.sub(u'-|\u2010|\u2011|\u2012|\u2013|\u2014|\u2015|%|\[|\]|:|\(|\)|/|\t', self.space_extend, text)
        text = text.strip(' \n')
        text = re.sub('\s+', ' ', text)
        return text

    def process(self, parsed_text):
        output = {'word': [], 'offsets': [], 'sentences': []}
        for token in parsed_text:
            output['word'].append(self._str(token.text))
            output['offsets'].append((token.idx, token.idx + len(token.text)))
        word_idx = 0
        for sent in parsed_text.sents:
            output['sentences'].append((word_idx, word_idx + len(sent)))
            word_idx += len(sent)
        assert word_idx == len(output['word'])
        return output

    def get_raw_context_offsets(self, words, raw_text):
        raw_context_offsets = []
        p = 0
        for token in words:
            while p < len(raw_text) and re.match('\s', raw_text[p]):
                p += 1
            if raw_text[p:p + len(token)] != token:
                print('something is wrong! token', token, 'raw_text:', raw_text)

            raw_context_offsets.append((p, p + len(token)))
            p += len(token)
        return raw_context_offsets

    def find_span(self, offsets, start, end):
        start_index = -1
        end_index = -1
        for i, offset in enumerate(offsets):
            if (start_index < 0) or (start >= offset[0]):
                start_index = i
            if (end_index < 0) and (end <= offset[1]):
                end_index = i
        return (start_index, end_index)

    def normalize_answer(self, s):
        def remove_articles(text):
            regex = re.compile(r'\b(a|an|the)\b', re.UNICODE)
            return re.sub(regex, ' ', text)

        def white_space_fix(text):
            return ' '.join(text.split())

        def remove_punc(text):
            exclude = set(string.punctuation)
            return ''.join(ch for ch in text if ch not in exclude)

        def lower(text):
            return text.lower()
        return white_space_fix(remove_articles(remove_punc(lower(s))))

    def find_span_with_gt(self, context, offsets, ground_truth):
        best_f1 = 0.0
        best_span = (len(offsets) - 1, len(offsets) - 1)
        gt = self.normalize_answer(self.pre_proc(ground_truth)).split()

        ls = [
            i for i in range(len(offsets))
            if context[offsets[i][0]:offsets[i][1]].lower() in gt
        ]

        for i in range(len(ls)):
            for j in range(i, len(ls)):
                pred = self.normalize_answer(
                    self.pre_proc(
                        context[offsets[ls[i]][0]:offsets[ls[j]][1]])).split()
                common = Counter(pred) & Counter(gt)
                num_same = sum(common.values())
                if num_same > 0:
                    precision = 1.0 * num_same / len(pred)
                    recall = 1.0 * num_same / len(gt)
                    f1 = (2 * precision * recall) / (precision + recall)
                    if f1 > best_f1:
                        best_f1 = f1
                        best_span = (ls[i], ls[j])
        return best_span

    def cut_sentence_old(self,doc_tok, r_spans, dataset_type, nlp_context):
        if dataset_type == "RG":
            l_r_start = [r_span[0] for r_span in r_spans]
            tok = []
            for i,j in nlp_context:
                if i not in l_r_start:
                    tok += doc_tok[i:j]
            doc_tok = tok
            return doc_tok
        else:
            return doc_tok

    def cut_sentence(self,doc_tok, r_spans, dataset_type):
        if dataset_type == "RG":
            arr = np.ones((len(doc_tok)))
            for r_span in r_spans:
                arr[r_span[0]:r_span[1]+1] = 0
            tok = []
            for i in range(len(doc_tok)):
                if arr[i]:
                    tok += doc_tok[i:i+1]
            doc_tok = tok
            return doc_tok
        else:
            return doc_tok

    def get_examples(self, data_dir, history_len, filename=None, threads=1,dataset_type = None, use_gpt = False, attention = False):
        if data_dir is None:
            data_dir = ""

        with open(
                os.path.join(data_dir, self.train_file if filename is None else filename), "r", encoding="utf-8"
        ) as reader:
            input_data = json.load(reader)

        threads = min(threads, cpu_count())
        with Pool(threads) as p:
            annotate_ = partial(self._create_examples, history_len=history_len, dataset_type = dataset_type, use_gpt = use_gpt, attention = attention)
            examples = list(tqdm(
                p.imap(annotate_, input_data),
                total=len(input_data),
                desc="Preprocessing examples",
            ))
        examples = [item for sublist in examples for item in sublist]
        return examples

    def _create_examples(self, input_data, history_len,dataset_type = None, use_gpt = False, attention = False):
        assert dataset_type in [None,'RG']
        if use_gpt:
            d_chatgpt = np.load("chatgpt_sents_d_hotpotqa.npy",allow_pickle=True)[()]
        nlp = spacy.load('en_core_web_sm', parser=False)
        examples = []
        datum = input_data
        context_str = datum['story']
        _datum = {
            'context': context_str,
            'id': datum['_id'],
            'supporting_facts': datum['supporting_facts']
        }
        nlp_context = nlp(self.pre_proc(context_str))
        _datum['annotated_context'] = self.process(nlp_context)
        _datum['raw_context_offsets'] = self.get_raw_context_offsets(_datum['annotated_context']['word'], context_str)
        question, answer = datum['question'], datum['answer']
        _qas = {
                'question': question,
                'answer': answer
            }
        _qas['raw_answer'] = answer

        if _qas['raw_answer'].lower() in ['yes', 'yes.']:
            _qas['raw_answer'] = 'yes'
        if _qas['raw_answer'].lower() in ['no', 'no.']:
            _qas['raw_answer'] = 'no'
        if _qas['raw_answer'].lower() in ['unknown', 'unknown.']:
            _qas['raw_answer'] = 'unknown'

        _qas['rational_span'] = []
        _qas['answer_span'] = []
        for (title, ind) in datum["supporting_facts"]:
            context_ind = [datum['context'][i][0] for i in range(len(datum['context']))].index(title)
            if ind>len(datum['context'][context_ind][1])-1:
                print("continue due to data error")
                continue
            s_rat = datum['context'][context_ind][1][ind]
            s_rat = re.sub('\s+', ' ', s_rat)
            s_rat = re.sub(r'^\s+', '', s_rat)
            s_rat = re.sub(r'\s+$', '', s_rat)
            start = _datum["context"].index(s_rat)
            end = start + len(s_rat)
            while len(s_rat) > 0 and self.is_whitespace(s_rat[0]):
                s_rat = s_rat[1:]
                start += 1
            while len(s_rat) > 0 and self.is_whitespace(s_rat[-1]):
                s_rat = s_rat[:-1]
                end -= 1
            r_start, r_end = self.find_span(_datum['raw_context_offsets'], start, end)
            input_text = _qas['answer'].strip().lower()
            if input_text in s_rat:
                p = s_rat.find(input_text)
                _qas['answer_span'] = self.find_span(_datum['raw_context_offsets'], start + p, start + p + len(input_text))
            _qas['rational_span'].append((r_start,r_end))

        if _qas['answer_span']==[]:
            _qas['answer_span'] = self.find_span_with_gt(_datum['context'], _datum['raw_context_offsets'], input_text)

        doc_tok = _datum['annotated_context']['word']
        #doc_tok = self.cut_sentence(doc_tok, _qas['rational_span'], dataset_type,_datum['annotated_context']['sentences'])
        doc_tok = self.cut_sentence(doc_tok, _qas['rational_span'], dataset_type)
        if len(doc_tok) == 0:
            return examples

        if dataset_type == "RG":
            _qas['rational_span'] = [(-1,-1)]
            gt = _qas['raw_answer']
            gt_context = nlp(self.pre_proc(gt))
            _gt = self.process(gt_context)['word']
            found = " ".join(doc_tok).find(gt)
            if gt not in ['unknown','yes','no']:
                if found == -1 and not attention:
                    if use_gpt:
                        try:
                            doc_tok.append(d_chatgpt[_datum["id"]])
                        except:
                            return examples
                    else:
                        doc_tok.append(gt)
                elif found != -1 and not attention:
                    r_start,r_end = -1,-1
                elif found == -1 and attention:
                    _qas['rational_span'] = [(len(doc_tok),len(doc_tok)+len(_gt)-1)]
                    doc_tok.extend(_gt)
                else:
                    for i in range(0,len(doc_tok)):
                        if doc_tok[i:i+len(_gt)] == _gt:
                            r_start = i 
                            r_end = r_start + len(_gt)-1
                            _qas['rational_span'] = [(r_start,r_end)]
                    #if r_start == r_end:
                    #    continue
            #elif attention:
            #    continue

        example = CoqaExample(
                qas_id = _datum['id'],
                question_text = '|Q| ' + _qas['question'],
                doc_tokens = doc_tok,
                orig_answer_text = _qas['raw_answer'] if dataset_type==None else 'unknown',
                start_position = _qas['answer_span'][0] if dataset_type==None else 0, 
                end_position = _qas['answer_span'][1] if dataset_type==None else 0,
                #rational_start_position = r_start, #AC: remove this
                #rational_end_position = r_end,     #AC: remove this
                rational_span = _qas['rational_span']
                #additional_answers=_qas['additional_answers'] if 'additional_answers' in _qas else None,
            )
        examples.append(example)

        return examples


def Extract_Feature_init(tokenizer_for_convert):
    global tokenizer
    tokenizer = tokenizer_for_convert

def Extract_Feature(example, tokenizer, max_seq_length = 512, doc_stride = 128, max_query_length = 64):
    features = []
    query_tokens = tokenizer.tokenize(example.question_text)

    cls_idx = 3
    if example.orig_answer_text == 'yes':
        cls_idx = 0  # yes
    elif example.orig_answer_text == 'no':
        cls_idx = 1  # no
    elif example.orig_answer_text == 'unknown':
        cls_idx = 2  # unknown

    if len(query_tokens) > max_query_length:
        # keep tail
        query_tokens = query_tokens[-max_query_length:]

    tok_to_orig_index = []
    orig_to_tok_index = []
    all_doc_tokens = []
    for (i, token) in enumerate(example.doc_tokens):
        orig_to_tok_index.append(len(all_doc_tokens))
        sub_tokens = tokenizer.tokenize(token)
        for sub_token in sub_tokens:
            tok_to_orig_index.append(i)
            all_doc_tokens.append(sub_token)

    tok_r = []
    for (start,end) in example.rational_span:
        tok_r_start_position = orig_to_tok_index[start] 
        if end < len(example.doc_tokens) - 1:
            tok_r_end_position = orig_to_tok_index[end + 1] - 1
        else:
            tok_r_end_position = len(all_doc_tokens) - 1
        tok_r.append((tok_r_start_position,tok_r_end_position))
        
    if cls_idx < 3:
        tok_start_position, tok_end_position = 0, 0
    else:
        tok_start_position = orig_to_tok_index[example.start_position]
        if example.end_position < len(example.doc_tokens) - 1:
            tok_end_position = orig_to_tok_index[example.end_position + 1] - 1
        else:
            tok_end_position = len(all_doc_tokens) - 1
        (tok_start_position, tok_end_position) = _improve_answer_span(
            all_doc_tokens, tok_start_position, tok_end_position, tokenizer,
            example.orig_answer_text)
        
    # The -4 accounts for <s>, </s></s> and </s>
    max_tokens_for_doc = max_seq_length - len(query_tokens) - 4

    _DocSpan = collections.namedtuple("DocSpan", ["start", "length"])
    doc_spans = []
    start_offset = 0
    while start_offset < len(all_doc_tokens):
        length = len(all_doc_tokens) - start_offset
        if length > max_tokens_for_doc:
            length = max_tokens_for_doc
        doc_spans.append(_DocSpan(start=start_offset, length=length))
        if start_offset + length == len(all_doc_tokens):
            break
        start_offset += min(length, doc_stride)

    for (doc_span_index, doc_span) in enumerate(doc_spans):
        slice_cls_idx = cls_idx
        tokens = []
        token_to_orig_map = {}
        token_is_max_context = {}
        tokens.append("<s>")
        for token in query_tokens:
            tokens.append(token)
        tokens.extend(["</s>","</s>"])

        for i in range(doc_span.length):
            split_token_index = doc_span.start + i
            token_to_orig_map[len(tokens)] = tok_to_orig_index[split_token_index]

            is_max_context = _check_is_max_context(doc_spans,
                                                   doc_span_index,
                                                   split_token_index)
            token_is_max_context[len(tokens)] = is_max_context
            tokens.append(all_doc_tokens[split_token_index])
        tokens.append("</s>")

        input_ids = tokenizer.convert_tokens_to_ids(tokens)

        # The mask has 1 for real tokens and 0 for padding tokens.
        input_mask = [1] * len(input_ids)
        segment_ids = [0]*max_seq_length
        # Zero-pad up to the sequence length.
        while len(input_ids) < max_seq_length:
            input_ids.append(1)
            input_mask.append(0)

        assert len(input_ids) == max_seq_length
        assert len(input_mask) == max_seq_length
        assert len(segment_ids) == max_seq_length

        # rational_part
        doc_start = doc_span.start
        doc_end = doc_span.start + doc_span.length - 1
        out_of_span = False
        for (tok_r_start_position,tok_r_end_position) in tok_r:
            if example.rational_span == [(-1,-1)] or not (
                tok_r_start_position >= doc_start and tok_r_end_position <= doc_end):
                out_of_span = True
                
        rational_mask = [0] * len(input_ids)
        if out_of_span:
            rational_start_position = 0
            rational_end_position = 0
        else:
            doc_offset = len(query_tokens) + 3
            for (tok_r_start_position,tok_r_end_position) in tok_r:
                rational_start_position = tok_r_start_position - doc_start + doc_offset
                rational_end_position = tok_r_end_position - doc_start + doc_offset
                rational_mask[rational_start_position:rational_end_position + 1] = [1] * (
                        rational_end_position - rational_start_position + 1)

        if cls_idx >= 3:
            # For training, if our document chunk does not contain an annotation we remove it
            doc_start = doc_span.start
            doc_end = doc_span.start + doc_span.length - 1
            out_of_span = False
            if not (tok_start_position >= doc_start and tok_end_position <= doc_end):
                out_of_span = True
            if out_of_span:
                start_position = 0
                end_position = 0
                slice_cls_idx = 2
            else:
                doc_offset = len(query_tokens) + 3
                start_position = tok_start_position - doc_start + doc_offset
                end_position = tok_end_position - doc_start + doc_offset
        else:
            start_position = 0
            end_position = 0

        features.append(
            CoqaFeatures(example_index=0,
                         unique_id=0,
                         doc_span_index=doc_span_index,
                         tokens=tokens,
                         token_to_orig_map=token_to_orig_map,
                         token_is_max_context=token_is_max_context,
                         input_ids=input_ids,
                         input_mask=input_mask,
                         segment_ids=segment_ids,
                         start_position=start_position,
                         end_position=end_position,
                         cls_idx=slice_cls_idx,
                         rational_mask=rational_mask))
    return features


def Extract_Features(examples, tokenizer, max_seq_length, doc_stride, max_query_length, is_training,threads=1):
    features = []
    threads = min(threads, cpu_count())
    with Pool(threads, initializer=Extract_Feature_init, initargs=(tokenizer,)) as p:
        annotate_ = partial(
            Extract_Feature,
            tokenizer=tokenizer,
            max_seq_length=max_seq_length,
            doc_stride=doc_stride,
            max_query_length=max_query_length,
        )
        features = list(
            tqdm(
                p.imap(annotate_, examples, chunksize=16),
                total=len(examples),
                desc="Extracting features from dataset",
            )
        )

    new_features = []
    unique_id = 1000000000
    example_index = 0
    for example_features in tqdm(features, total=len(features), desc="Tag unique id to each example"):
        if not example_features:
            continue
        for example_feature in example_features:
            example_feature.example_index = example_index
            example_feature.unique_id = unique_id
            new_features.append(example_feature)
            unique_id += 1
        example_index += 1
    features = new_features
    del new_features
    all_input_ids = torch.tensor([f.input_ids for f in features], dtype=torch.long)
    all_input_mask = torch.tensor([f.input_mask for f in features], dtype=torch.long)
    all_tokentype_ids = torch.tensor([f.segment_ids for f in features], dtype=torch.long)
    if not is_training:
        all_example_index = torch.arange(all_input_ids.size(0), dtype=torch.long)
        dataset = TensorDataset(all_input_ids, all_tokentype_ids, all_input_mask, all_example_index)
    else:
        all_start_positions = torch.tensor([f.start_position for f in features], dtype=torch.long)
        all_end_positions = torch.tensor([f.end_position for f in features], dtype=torch.long)
        all_rational_mask = torch.tensor([f.rational_mask for f in features], dtype=torch.long)
        all_cls_idx = torch.tensor([f.cls_idx for f in features], dtype=torch.long)
        dataset = TensorDataset(all_input_ids, all_tokentype_ids, all_input_mask, all_start_positions,
                                all_end_positions, all_rational_mask, all_cls_idx)

    return features, dataset
