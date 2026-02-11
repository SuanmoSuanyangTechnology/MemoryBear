# import os
# from app.core.memory.agent.utils.llm_tools import (
#     PROJECT_ROOT_
# )
# from transformers import AutoTokenizer, AutoModelForSequenceClassification
# import torch
# import torch.nn.functional as F
# model_path = os.path.join(PROJECT_ROOT_, 'models', 'model', 'mDeBERTa-v3-base-mnli-xnli')
# tokenizer = AutoTokenizer.from_pretrained(model_path)
# model = AutoModelForSequenceClassification.from_pretrained(model_path)
#
# labels = ["contradiction", "neutral", "entailment"]
# def nli(premise, hypothesis):
#     inputs = tokenizer(premise, hypothesis, return_tensors="pt", truncation=True)
#     outputs = model(**inputs)
#     probs = F.softmax(outputs.logits, dim=1)
#     label_id = torch.argmax(probs).item()
#     return labels[label_id], probs[0][label_id].item()
