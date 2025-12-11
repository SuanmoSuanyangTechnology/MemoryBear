import copy
import re
from io import BytesIO
from PIL import Image

from app.core.rag.nlp import tokenize, is_english
from app.core.rag.nlp import rag_tokenizer
from app.core.rag.deepdoc.parser import PdfParser, PlainParser
from app.core.rag.deepdoc.parser.ppt_parser import RAGPptParser as PptParser
from PyPDF2 import PdfReader as pdf2_read
from app.core.rag.app.naive import by_plaintext, PARSERS

class Ppt(PptParser):
    def __call__(self, fnm, from_page, to_page, callback=None):
        txts = super().__call__(fnm, from_page, to_page)

        callback(0.5, "Text extraction finished.")
        import aspose.slides as slides
        import aspose.pydrawing as drawing
        imgs = []
        with slides.Presentation(BytesIO(fnm)) as presentation:
            for i, slide in enumerate(presentation.slides[from_page: to_page]):
                try:
                    with BytesIO() as buffered:
                        slide.get_thumbnail(
                            0.1, 0.1).save(
                            buffered, drawing.imaging.ImageFormat.jpeg)
                        buffered.seek(0)
                        imgs.append(Image.open(buffered).copy())
                except RuntimeError as e:
                    raise RuntimeError(f'ppt parse error at page {i+1}, original error: {str(e)}') from e
        assert len(imgs) == len(
            txts), "Slides text and image do not match: {} vs. {}".format(len(imgs), len(txts))
        callback(0.9, "Image extraction finished")
        self.is_english = is_english(txts)
        return [(txts[i], imgs[i]) for i in range(len(txts))]

class Pdf(PdfParser):
    def __init__(self):
        super().__init__()

    def __garbage(self, txt):
        txt = txt.lower().strip()
        if re.match(r"[0-9\.,%/-]+$", txt):
            return True
        if len(txt) < 3:
            return True
        return False

    def __call__(self, filename, binary=None, from_page=0,
                 to_page=100000, zoomin=3, callback=None):
        from timeit import default_timer as timer
        start = timer()
        callback(msg="OCR started")
        self.__images__(filename if not binary else binary,
                        zoomin, from_page, to_page, callback)
        callback(msg="Page {}~{}: OCR finished ({:.2f}s)".format(from_page, min(to_page, self.total_page), timer() - start))
        assert len(self.boxes) == len(self.page_images), "{} vs. {}".format(
            len(self.boxes), len(self.page_images))
        res = []
        for i in range(len(self.boxes)):
            lines = "\n".join([b["text"] for b in self.boxes[i]
                              if not self.__garbage(b["text"])])
            res.append((lines, self.page_images[i]))
        callback(0.9, "Page {}~{}: Parsing finished".format(
            from_page, min(to_page, self.total_page)))
        return res, []


class PlainPdf(PlainParser):
    def __call__(self, filename, binary=None, from_page=0,
                 to_page=100000, callback=None, **kwargs):
        self.pdf = pdf2_read(filename if not binary else BytesIO(binary))
        page_txt = []
        for page in self.pdf.pages[from_page: to_page]:
            page_txt.append(page.extract_text())
        callback(0.9, "Parsing finished")
        return [(txt, None) for txt in page_txt], []


def chunk(filename, binary=None, from_page=0, to_page=100000,
          lang="Chinese", callback=None, vision_model=None, parser_config=None, **kwargs):
    """
    The supported file formats are pdf, pptx.
    Every page will be treated as a chunk. And the thumbnail of every page will be stored.
    PPT file will be parsed by using this method automatically, setting-up for every PPT file is not necessary.
    """
    if parser_config is None:
        parser_config = {}
    eng = lang.lower() == "english"
    doc = {
        "docnm_kwd": filename,
        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))
    }
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
    res = []
    if re.search(r"\.pptx?$", filename, re.IGNORECASE):
        if not binary:
            with open(filename, "rb") as f:
                binary = f.read()
        ppt_parser = Ppt()
        for pn, (txt, img) in enumerate(ppt_parser(
                filename if not binary else binary, from_page, 1000000, callback)):
            d = copy.deepcopy(doc)
            pn += from_page
            d["image"] = img
            d["doc_type_kwd"] = "image"
            d["page_num_int"] = [pn + 1]
            d["top_int"] = [0]
            d["position_int"] = [(pn + 1, 0, img.size[0], 0, img.size[1])]
            tokenize(d, txt, eng)
            res.append(d)
        return res
    elif re.search(r"\.pdf$", filename, re.IGNORECASE):
        layout_recognizer = parser_config.get("layout_recognize", "DeepDOC")

        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "Plain Text"

        name = layout_recognizer.strip().lower()
        parser = PARSERS.get(name, by_plaintext)
        callback(0.1, "Start to parse.")

        sections, _, _ = parser(
            filename=filename,
            binary=binary,
            from_page=from_page,
            to_page=to_page,
            lang=lang,
            callback=callback,
            vision_model=vision_model,
            pdf_cls=Pdf,
            **kwargs
        )

        if not sections:
            return []

        if name in ["tcadp", "docling", "mineru"]:
            parser_config["chunk_token_num"] = 0
        
        callback(0.8, "Finish parsing.")

        for pn, (txt, img) in enumerate(sections):
            d = copy.deepcopy(doc)
            pn += from_page
            if img:
                d["image"] = img
            d["page_num_int"] = [pn + 1]
            d["top_int"] = [0]
            d["position_int"] = [(pn + 1, 0, img.size[0] if img else 0, 0, img.size[1] if img else 0)]
            tokenize(d, txt, eng)
            res.append(d)
        return res

    raise NotImplementedError(
        "file type not supported yet(pptx, pdf supported)")


if __name__ == "__main__":
    import sys

    def dummy(a, b):
        pass
    chunk(sys.argv[1], callback=dummy)
