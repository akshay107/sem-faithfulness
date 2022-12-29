# Usage instructions

1) Copy the dataset and place in ./data, files can be downloaded from 

2) Run main.py with
```
python main.py --train [O|C] --eval [O|TS|RG] --output [directory]
```

--train O for original training
--train C for combined training
--eval O for eval on original dataset
--eval TS for eval on TS dataset
--eval RG for eval on TS-R dataset
--output is the ouput directory for saving/loading weights and saving predictions and logs
e.g.

```
python main.py --train O --eval O --output XLNet_orig 	#(original training and eval on O)
python main.py --eval TS --output XLNet_orig 		#(evaluate the model at XLNet_orig on TS dataset)
python main.py --train C --eval O --output XLNet_orig 	#(combined training and eval on O)
```

3) Following eval you will have a predict_normal_det.json file at the provided directory. Then run

```
python ./results/convert_coqa.py --input_file ./[directory]/predict_normal_det.json --output_file pred.json
python evaluate-v1.0.py --data-file data/coqa-dev-v1.0.json --pred-file pred.json
```

4) Steps 2, 3 needs to be repeated for original and combined training and evaluation on all datasets.
