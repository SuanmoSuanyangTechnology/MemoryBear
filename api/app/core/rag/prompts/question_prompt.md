## Role
You are a text analyzer and knowledge extraction expert.

## Task
Generate {{ topn }} question-answer pairs from the given text content.

## Requirements
- Understand and summarize the text content, and generate the top {{ topn }} important question-answer pairs.
- Each question-answer pair MUST be on a single line, formatted as: Q: <question> A: <answer>
- The questions SHOULD NOT have overlapping meanings.
- The questions SHOULD cover the main content of the text as much as possible.
- The answers MUST be concise, accurate, and directly derived from the text content.
- The answers SHOULD be self-contained and understandable without additional context.
- Both questions and answers MUST be in the same language as the given text content.
- Output question-answer pairs ONLY, no extra explanation.

## Example Output
Q: What is the capital of France? A: The capital of France is Paris.
Q: When was the Eiffel Tower built? A: The Eiffel Tower was built in 1889.

---

## Text Content
{{ content }}
