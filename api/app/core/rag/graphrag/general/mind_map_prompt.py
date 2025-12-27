MIND_MAP_EXTRACTION_PROMPT = """
- Role: You're a talent text processor to summarize a piece of text into a mind map.

- Step of task:
  1. Generate a title for user's 'TEXT'。
  2. Classify the 'TEXT' into sections of a mind map.
  3. If the subject matter is really complex, split them into sub-sections and sub-subsections. 
  4. Add a shot content summary of the bottom level section.

- Output requirement:
  - Generate at least 4 levels.
  - Always try to maximize the number of sub-sections. 
  - In language of 'Text'
  - MUST IN FORMAT OF MARKDOWN

-TEXT-
{input_text}

"""