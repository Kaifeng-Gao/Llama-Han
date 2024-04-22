import yaml
import torch
from datasets import load_dataset
from peft import LoraConfig, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
import transformers, evaluate
import argparse
import sys


def load_config(config_path):
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

def parse_args():
    parser = argparse.ArgumentParser(description="Load and fine-tune a model")
    parser.add_argument('--model_path', type=str, help='Path to the base model')
    parser.add_argument('--new_model_path', type=str, help='Path for saving the fine-tuned model')
    parser.add_argument('--dataset_path', type=str, help='Path to the dataset')
    parser.add_argument('--dataset_config', type=str, help='Dataset configuration')
    parser.add_argument('--num_shots', type=int, help='Number of shots in instruction')
    parser.add_argument('--finetune', type=bool, help='whether to use finetuned model')
    return parser.parse_args()

# Load configurations from YAML file
config = load_config('config.yaml')

# Setup configurations
args = parse_args()
model_cfg = config['model_config']
eval_cfg = config['eval_config']
access_token = config['access_token']

# Initialize Configurations
model_path = args.model_path if args.model_path else model_cfg['model_path']
new_model_path = args.new_model_path if args.new_model_path else model_cfg['new_model_path']
dataset_path = args.dataset_path if args.dataset_path else model_cfg['dataset_path']
dataset_config = args.dataset_config if args.dataset_config else model_cfg['dataset_config']
num_shots = args.num_shots if args.num_shots else eval_cfg['num_shots']
finetune = args.finetune if args.finetune else False
cot = args.cot if args.cot else eval_cfg['cot']

print('=' * 50)
print("------------- eval_config -------------")
print(f"Configuration used:")
print(f"Model Path: {model_path}")
print(f"New Model Path: {new_model_path}")
print(f"Dataset Path: {dataset_path}")
print(f"Dataset Config: {dataset_config}")
print(f"Number of Shots: {num_shots}")
print(f"Use Finetune Model: {finetune}")

# Load model and tokenizer
model = AutoModelForCausalLM.from_pretrained(
    model_path, 
    device_map="auto", 
    token=access_token['huggingface_token']
)
tokenizer = AutoTokenizer.from_pretrained(
    model_path, 
    use_fast=True,
    token=access_token['huggingface_token']
)
if finetune:
    model = PeftModel.from_pretrained(model, new_model_path)
    model = model.merge_and_unload()
tokenizer.pad_token = tokenizer.eos_token
tokenizer.padding_side = "right"

# Initialize pipeline
pipeline = transformers.pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    model_kwargs={"torch_dtype": torch.bfloat16}
)
terminators = [
    pipeline.tokenizer.eos_token_id,
    pipeline.tokenizer.convert_tokens_to_ids("<|eot_id|>")
]

# Initialize test dataset
dataset = load_dataset(dataset_path, dataset_config)
if cot:
    dataset_train, dataset_test = dataset.select_columns(["classical", "modern", "english"]).values()
else:
    dataset_train, dataset_test = dataset.select_columns(["classical", "english"]).values()
dataset_examples = dataset_test.select(range(num_shots))
dataset_predict = dataset_test.select(range(num_shots, len(dataset_test)))

if cot:
    prompt_template = "Classical: {classical}\nModern: {modern}\nEnglish: {english}"
else:
    prompt_template = "Classical: {classical}\nEnglish: {english}"
if num_shots == 0:
    prompt_examples = ""
else:
    prompt_examples = "\n\n".join([prompt_template.format(**row) for row in dataset_examples])

# Initialize test prompts and reference translations
prompts = []
references = []
for row in dataset_predict:
    message = []
    if cot:
        prompt= prompt_examples + "\n"\
            + "Translate the following sentence from classical Chinese into modern Chinese and then into English. Provide only the translation:" \
            + prompt_template.format(classical=row["classical"], modern="", english="")[:-1]
        
    else:
        prompt= prompt_examples + "\n"\
            + "Translate the following sentence from Classical Chinese into English. Provide only the translation:" \
            + prompt_template.format(classical=row["classical"], english="")[:-1]
    message.append({"role": "user", "content": prompt})
    message = pipeline.tokenizer.apply_chat_template(
        message, 
        tokenize=False, 
        add_generation_prompt=True)
    reference=row["english"]
    prompts.append(message)
    references.append([reference])

# Outputs
# prompts, references = prompts[:10], references[:10]
print("# of test samples:", len(prompts))
outputs = pipeline(
    prompts,
    max_new_tokens=256,
    eos_token_id=terminators,
    do_sample=True,
    temperature=0.7,
    top_p=0.9,
)
predictions = [output[0]['generated_text'][len(prompt):] for output, prompt in zip(outputs, prompts)]

# Evaluation
sacrebleu = evaluate.load("sacrebleu")
sacrebleu_results=sacrebleu.compute(predictions=predictions, references=references)
print("------------- sacrebleu_results -------------")
print(sacrebleu_results)

meteor = evaluate.load('meteor')
meteor_results = meteor.compute(predictions=predictions, references=references)
print("------------- meteor_results -------------")
print(meteor_results)

chrf = evaluate.load("chrf")
chrf_results = chrf.compute(predictions=predictions, references=references)
print("------------- chrf_results -------------")
print(chrf_results)

# Example
print("")
print("------------- example -------------")
for i in range(10):
    print("system:", predictions[i])
    print("reference:", references[i][0])

