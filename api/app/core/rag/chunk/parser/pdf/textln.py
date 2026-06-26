import json
import logging
import os
import re
import sys
import threading
from io import BytesIO
from os import PathLike
from typing import Callable, Optional

import numpy as np
import pdfplumber
import requests
from PIL import Image

from app.core.rag.chunk.parser.base import DocumentParser

LOCK_KEY_PDFPLUMBER = "global_shared_lock_pdfplumber"
if LOCK_KEY_PDFPLUMBER not in sys.modules:
    sys.modules[LOCK_KEY_PDFPLUMBER] = threading.Lock()


class TextLnParser:
    def __init__(self, textln_api: str, app_id: str, secret_code: str):
        self.textln_api = textln_api
        self.app_id = app_id
        self.secret_code = secret_code

    def recognize(self, file_content: bytes, options: dict) -> str:
        params = {key: str(value) for key, value in options.items()}
        headers = {
            "x-ti-app-id": self.app_id,
            "x-ti-secret-code": self.secret_code,
            "Content-Type": "application/octet-stream",
        }

        response = requests.post(
            url=self.textln_api,
            params=params,
            headers=headers,
            data=file_content,
        )
        response.raise_for_status()
        return response.text

    def __images__(self, filename, zoomin: int = 1, page_from=0, page_to=600, callback=None):
        self.page_from = page_from
        self.page_to = page_to
        try:
            with pdfplumber.open(filename) if isinstance(filename, (str, PathLike)) else pdfplumber.open(
                BytesIO(filename)
            ) as pdf:
                self.pdf = pdf
                self.page_images = [
                    page.to_image(resolution=72 * zoomin, antialias=True).original
                    for page in self.pdf.pages[page_from:page_to]
                ]
        except Exception as error:
            self.page_images = None
            logging.exception(error)

    def parse_pdf(
        self,
        filepath: str | PathLike[str],
        binary: BytesIO | bytes,
        callback: Optional[Callable] = None,
        vision_model=None,
        lang: Optional[str] = None,
        **kwargs,
    ):
        try:
            callback(0.15, "USE [Textln] to recognize the file")
            self.__images__(filepath, zoomin=1)
            base_name, _ = os.path.splitext(filepath)
            if not os.path.exists(f"{base_name}_result.md"):
                with open(filepath, "rb") as file:
                    file_content = file.read()
                options = {
                    "dpi": 144,
                    "get_image": "objects",
                    "markdown_details": 1,
                    "page_count": 1000,
                    "parse_mode": "auto",
                    "table_flavor": "md",
                }
                response = self.recognize(file_content, options)
                with open(f"{base_name}_result.json", "w", encoding="utf-8") as output:
                    output.write(response)
                json_response = json.loads(response)
                if "result" in json_response and "markdown" in json_response["result"]:
                    markdown_content = json_response["result"]["markdown"]
                    with open(f"{base_name}_result.md", "w", encoding="utf-8") as output:
                        output.write(markdown_content)
                else:
                    callback(prog=-1, msg=json_response["message"])
                    return None, None, None
            callback(0.75, f"[Textln] respond md: {base_name}_result.md")

            from app.core.rag.chunk.parser.markdown import MarkdownParser

            parser_config = kwargs.get(
                "parser_config",
                {
                    "layout_recognize": "TextLn",
                    "chunk_token_num": 512,
                    "delimiter": "\n!?。；！？",
                    "analyze_hyperlink": True,
                },
            )
            markdown_parser = MarkdownParser(int(parser_config.get("chunk_token_num", 128)))
            sections, tables = markdown_parser(
                f"{base_name}_result.md",
                binary,
                separate_tables=False,
                delimiter=parser_config.get("delimiter", "\n!?;。；！？"),
            )
            return sections, tables
        except Exception as error:
            logging.warning(f"Error: {error}")
            callback(prog=-1, msg=str(error))
        return None, None

    @staticmethod
    def extract_positions(text: str):
        positions = []
        for tag in re.findall(r"@@[0-9-]+\t[0-9.\t]+##", text):
            page_numbers, left, right, top, bottom = tag.strip("#").strip("@").split("\t")
            left, right, top, bottom = float(left), float(right), float(top), float(bottom)
            positions.append(
                ([int(page_number) - 1 for page_number in page_numbers.split("-")], left, right, top, bottom)
            )
        return positions

    def crop(self, text, ZM=1, need_position=False):
        images = []
        extracted_positions = self.extract_positions(text)
        if not extracted_positions:
            if need_position:
                return None, None
            return

        max_width = max(np.max([right - left for (_, left, right, _, _) in extracted_positions]), 6)
        gap = 6
        first_position = extracted_positions[0]
        extracted_positions.insert(
            0,
            (
                [first_position[0][0]],
                first_position[1],
                first_position[2],
                max(0, first_position[3] - 120),
                max(first_position[3] - gap, 0),
            ),
        )
        last_position = extracted_positions[-1]
        extracted_positions.append(
            (
                [last_position[0][-1]],
                last_position[1],
                last_position[2],
                min(self.page_images[last_position[0][-1]].size[1], last_position[4] + gap),
                min(self.page_images[last_position[0][-1]].size[1], last_position[4] + 120),
            )
        )

        positions = []
        for index, (page_numbers, left, right, top, bottom) in enumerate(extracted_positions):
            right = left + max_width

            if bottom <= top:
                bottom = top + 2

            for page_number in page_numbers[1:]:
                bottom += self.page_images[page_number - 1].size[1]

            first_image = self.page_images[page_numbers[0]]
            x0, y0, x1, y1 = int(left), int(top), int(right), int(min(bottom, first_image.size[1]))
            cropped_image = first_image.crop((x0, y0, x1, y1))
            images.append(cropped_image)
            if 0 < index < len(extracted_positions) - 1:
                positions.append((page_numbers[0] + self.page_from, x0, x1, y0, y1))

            bottom -= first_image.size[1]
            for page_number in page_numbers[1:]:
                page = self.page_images[page_number]
                x0, y0, x1, y1 = int(left), 0, int(right), int(min(bottom, page.size[1]))
                cropped_page = page.crop((x0, y0, x1, y1))
                images.append(cropped_page)
                if 0 < index < len(extracted_positions) - 1:
                    positions.append((page_number + self.page_from, x0, x1, y0, y1))
                bottom -= page.size[1]

        if not images:
            if need_position:
                return None, None
            return

        height = sum(image.size[1] + gap for image in images)
        width = int(np.max([image.size[0] for image in images]))
        picture = Image.new("RGB", (width, int(height)), (245, 245, 245))
        height = 0
        for index, image in enumerate(images):
            if index == 0 or index + 1 == len(images):
                image = image.convert("RGBA")
                overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
                overlay.putalpha(128)
                image = Image.alpha_composite(image, overlay).convert("RGB")
            picture.paste(image, (0, int(height)))
            height += image.size[1] + gap

        if need_position:
            return picture, positions
        return picture

    @staticmethod
    def remove_tag(text):
        return re.sub(r"@@[\t0-9.-]+?##", "", text)


class TextLnPdfParser(DocumentParser):
    def parse(self, ctx):
        textln_api = os.environ.get("TEXTLN_APISERVER", "https://api.textin.com/ai/service/v1/pdf_to_markdown")
        app_id = os.environ.get("TEXTLN_APP_ID", "")
        secret_code = os.environ.get("TEXTLN_SECRET_CODE", "")
        pdf_parser = TextLnParser(textln_api=textln_api, app_id=app_id, secret_code=secret_code)

        sections, tables = pdf_parser.parse_pdf(
            filepath=ctx.filename,
            binary=ctx.binary,
            callback=ctx.callback,
            vision_model=ctx.vision_model,
            lang=ctx.lang,
            **ctx.kwargs,
        )
        return sections, tables, pdf_parser
