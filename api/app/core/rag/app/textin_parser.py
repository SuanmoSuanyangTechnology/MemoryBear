import json
import os
import re
import sys
import threading
from io import BytesIO
from os import PathLike
from typing import Any, Callable, Optional
import numpy as np
import pdfplumber
from functools import reduce
import requests
import logging
from PIL import Image

from app.core.rag.nlp import concat_img
from app.core.rag.deepdoc.parser.figure_parser import VisionFigureParser

LOCK_KEY_pdfplumber = "global_shared_lock_pdfplumber"
if LOCK_KEY_pdfplumber not in sys.modules:
    sys.modules[LOCK_KEY_pdfplumber] = threading.Lock()


class TextLnParser:
    def __init__(self, textln_api: str, app_id: str, secret_code: str):
        self.textln_api = textln_api
        self.app_id = app_id
        self.secret_code = secret_code

    def recognize(self, file_content: bytes, options: dict) -> str:
        params = {}
        for key, value in options.items():
            params[key] = str(value)

        headers = {
            "x-ti-app-id": self.app_id,
            "x-ti-secret-code": self.secret_code,
            "Content-Type": "application/octet-stream"
        }

        response = requests.post(
            url=self.textln_api,
            params=params,
            headers=headers,
            data=file_content
        )

        response.raise_for_status()
        return response.text

    def __images__(self, fnm, zoomin: int = 1, page_from=0, page_to=600, callback=None):
        self.page_from = page_from
        self.page_to = page_to
        try:
            with pdfplumber.open(fnm) if isinstance(fnm, (str, PathLike)) else pdfplumber.open(BytesIO(fnm)) as pdf:
                self.pdf = pdf
                self.page_images = [p.to_image(resolution=72 * zoomin, antialias=True).original for _, p in enumerate(self.pdf.pages[page_from:page_to])]
        except Exception as e:
            self.page_images = None
            logging.exception(e)


    def parse_pdf(
        self,
        filepath: str | PathLike[str],
        binary: BytesIO | bytes,
        callback: Optional[Callable] = None,
        vision_model=None,
        lang: Optional[str] = None,
        **kwargs
    ):
        try:
            callback(0.15, "USE [Textln] to recognize the file")
            self.__images__(filepath, zoomin=1)
            base_name, ext = os.path.splitext(filepath)
            if not os.path.exists(f"{base_name}_result.md"):
                with open(filepath, "rb") as f:
                    file_content = f.read()
                options = dict(
                    dpi=144,
                    get_image="objects",
                    markdown_details=1,
                    page_count=1000,  # 当上传的是pdf时，表示要进行解析的pdf页数。总页数不得超过1000页，默认为1000页
                    parse_mode="auto",
                    table_flavor="md"
                )
                response = self.recognize(file_content, options)
                # 保存完整的JSON响应到result.json文件
                with open(f"{base_name}_result.json", "w", encoding="utf-8") as f:
                    f.write(response)
                # 解析JSON响应以提取markdown内容
                json_response = json.loads(response)
                if "result" in json_response and "markdown" in json_response["result"]:
                    markdown_content = json_response["result"]["markdown"]
                    with open(f"{base_name}_result.md", "w", encoding="utf-8") as f:
                        f.write(markdown_content)
                else:
                    callback(prog=-1, msg=json_response["message"])
                    return None, None, None
            callback(0.75, f"[Textln] respond md: {base_name}_result.md")

            from app.core.rag.app.naive import Markdown
            parser_config = kwargs.get(
                "parser_config", {
                    "layout_recognize": "TextLn", "chunk_token_num": 512, "delimiter": "\n!?。；！？",
                    "analyze_hyperlink": True})
            markdown_parser = Markdown(int(parser_config.get("chunk_token_num", 128)))
            sections, tables = markdown_parser(f"{base_name}_result.md", binary, separate_tables=False,
                                               delimiter=parser_config.get("delimiter", "\n!?;。；！？"))
            return sections, tables
            # # Process images for each section
            # section_images = []
            # if vision_model:
            #     for idx, (section_text, _) in enumerate(sections):
            #         images = markdown_parser.get_pictures(section_text) if section_text else None
            #
            #         if images:
            #             # If multiple images found, combine them using concat_img
            #             combined_image = reduce(concat_img, images) if len(images) > 1 else images[0]
            #             section_images.append(combined_image)
            #             markdown_vision_parser = VisionFigureParser(vision_model=vision_model, figures_data=[
            #                 ((combined_image, ["markdown image"]), [(0, 0, 0, 0, 0)])], **kwargs)
            #             boosted_figures = markdown_vision_parser(callback=callback)
            #             sections[idx] = (section_text + "\n\n" + "\n\n".join([fig[0][1][0] for fig in boosted_figures]),
            #                              sections[idx][1])
            #         else:
            #             section_images.append(None)
            #
            # else:
            #     logging.warning("No visual model detected. Skipping figure parsing enhancement.")
            # return sections, tables, section_images
        except Exception as e:
            logging.warning(f"Error: {e}")
            callback(prog=-1, msg=str(e))
        return None, None

    @staticmethod
    def extract_positions(txt: str):
        poss = []
        for tag in re.findall(r"@@[0-9-]+\t[0-9.\t]+##", txt):
            pn, left, right, top, bottom = tag.strip("#").strip("@").split("\t")
            left, right, top, bottom = float(left), float(right), float(top), float(bottom)
            poss.append(([int(p) - 1 for p in pn.split("-")], left, right, top, bottom))
        return poss

    def crop(self, text, ZM=1, need_position=False):
        imgs = []
        poss = self.extract_positions(text)
        if not poss:
            if need_position:
                return None, None
            return

        max_width = max(np.max([right - left for (_, left, right, _, _) in poss]), 6)
        GAP = 6
        pos = poss[0]
        poss.insert(0, ([pos[0][0]], pos[1], pos[2], max(0, pos[3] - 120), max(pos[3] - GAP, 0)))
        pos = poss[-1]
        poss.append(([pos[0][-1]], pos[1], pos[2], min(self.page_images[pos[0][-1]].size[1], pos[4] + GAP), min(self.page_images[pos[0][-1]].size[1], pos[4] + 120)))

        positions = []
        for ii, (pns, left, right, top, bottom) in enumerate(poss):
            right = left + max_width

            if bottom <= top:
                bottom = top + 2

            for pn in pns[1:]:
                bottom += self.page_images[pn - 1].size[1]

            img0 = self.page_images[pns[0]]
            x0, y0, x1, y1 = int(left), int(top), int(right), int(min(bottom, img0.size[1]))
            crop0 = img0.crop((x0, y0, x1, y1))
            imgs.append(crop0)
            if 0 < ii < len(poss) - 1:
                positions.append((pns[0] + self.page_from, x0, x1, y0, y1))

            bottom -= img0.size[1]
            for pn in pns[1:]:
                page = self.page_images[pn]
                x0, y0, x1, y1 = int(left), 0, int(right), int(min(bottom, page.size[1]))
                cimgp = page.crop((x0, y0, x1, y1))
                imgs.append(cimgp)
                if 0 < ii < len(poss) - 1:
                    positions.append((pn + self.page_from, x0, x1, y0, y1))
                bottom -= page.size[1]

        if not imgs:
            if need_position:
                return None, None
            return

        height = 0
        for img in imgs:
            height += img.size[1] + GAP
        height = int(height)
        width = int(np.max([i.size[0] for i in imgs]))
        pic = Image.new("RGB", (width, height), (245, 245, 245))
        height = 0
        for ii, img in enumerate(imgs):
            if ii == 0 or ii + 1 == len(imgs):
                img = img.convert("RGBA")
                overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                overlay.putalpha(128)
                img = Image.alpha_composite(img, overlay).convert("RGB")
            pic.paste(img, (0, int(height)))
            height += img.size[1] + GAP

        if need_position:
            return pic, positions
        return pic

    @staticmethod
    def remove_tag(txt):
        return re.sub(r"@@[\t0-9.-]+?##", "", txt)


