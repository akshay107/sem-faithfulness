# Usage instructions

1) Copy the dataset and place in ./data, files can be downloaded from 

2) Run main.py with
```
python main.py --train [O|C] --eval [O|TS|RG] --output [directory]
```

--train O for original training
--train C for intervention-based training
--eval O for eval on original dataset
--eval TS for eval on TS dataset
--eval RG for eval on TS-R dataset
--output is the ouput directory for saving/loading weights and saving predictions and logs
e.g.

```
python main.py --train O --eval O --output Roberta_orig 	#(original training and eval on OS)
python main.py --eval TS --output Roberta_orig 			#(evaluate the model at Roberta_orig on TS dataset)
python main.py --train C --eval O --output Roberta_orig 	#(combined training and eval on OS)
```

3) Following eval you will have a predictions.json file at the provided directory. Then run
```
python evaluate-v1.0.py --data-file data/coqa-dev-v1.0.json --pred-file [directory]/predictions.json
```

4) Steps 2, 3 needs to be repeated for original and combined training and evaluation on all datasets.
