# Semantic Faithfullness of Language Models

Code generating results for this [paper](https://arxiv.org/abs/2212.10696).
Implementation of Intervention Based Training for question answering (CoQA and HotpotQA dataset) on BERT, RoBERTa and XLNet.

## Features
1) Train on the original dataset
2) Train on the combined OS, TS, TS-R dataset
3) Evaluation on OS,TS, TS-R

## Usage
#### Clone this repository
#### Install the necessary requirements in the environment.yml file
We recommend anaconda for this.

```
conda env create -f environment.yml
conda activate RobustProject
```
#### Download the datasets.
Datasets for negation intervention, predicate argument structure experiments, and the Aug dataset (corresponding to TS-R+Aug for CoQA and OS-R+Aug for HotpotQA) is available [here](https://drive.google.com/drive/u/0/folders/1gHHPyjgkhgVNlVwQ16bA54_wt6eM_bfH).

#### Follow the instructions in the corresponding folder
