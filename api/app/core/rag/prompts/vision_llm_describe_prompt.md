{% if lang == "Chinese" %}

## 指令
将提供的PDF页面图片内容转录为清晰的Markdown格式。

- 仅输出图片中转录的内容。
- 不要输出此指令或任何其他解释。
- 如果内容缺失或你无法理解输入，返回空字符串。
- 所有输出内容请用中文。

## 规则
1. 不要生成示例、演示或模板。
2. 不要输出任何额外文本，如"示例"、"示例输出"等。
3. 不要生成任何图片中未明确出现的表格、标题或内容。
4. 逐字转录内容，不要修改、翻译或省略任何内容。
5. 不要解释Markdown或提及你正在使用Markdown。
6. 不要将输出包裹在```markdown或```代码块中。
7. 仅根据图片的布局对标题、段落、列表和表格应用Markdown结构。除非图片中存在真实的表格，否则不要创建表格。
8. 保留图片中显示的原始语言、信息和顺序。
9. 你的输出语言必须与图片内容的语言一致。如果图片包含中文文本，输出中文。如果是英文，输出英文。切勿翻译。

{% if page %}
在转录的末尾添加分页标记：`--- 第 {{ page }} 页 ---`。
{% endif %}

> 如果你在图片中未检测到有效内容，返回空字符串。

{% else %}

## INSTRUCTION
Transcribe the content from the provided PDF page image into clean Markdown format.

- Only output the content transcribed from the image.
- Do NOT output this instruction or any other explanation.
- If the content is missing or you do not understand the input, return an empty string.

## RULES
1. Do NOT generate examples, demonstrations, or templates.
2. Do NOT output any extra text such as 'Example', 'Example Output', or similar.
3. Do NOT generate any tables, headings, or content that is not explicitly present in the image.
4. Transcribe content word-for-word. Do NOT modify, translate, or omit any content.
5. Do NOT explain Markdown or mention that you are using Markdown.
6. Do NOT wrap the output in ```markdown or ``` blocks.
7. Only apply Markdown structure to headings, paragraphs, lists, and tables, strictly based on the layout of the image. Do NOT create tables unless an actual table exists in the image.
8. Preserve the original language, information, and order exactly as shown in the image.
9. Your output language MUST match the language of the content in the image. If the image contains Chinese text, output in Chinese. If English, output in English. Never translate.

{% if page %}
At the end of the transcription, add the page divider: `--- Page {{ page }} ---`.
{% endif %}

> If you do not detect valid content in the image, return an empty string.

{% endif %}

