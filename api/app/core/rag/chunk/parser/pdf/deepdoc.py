import logging
from timeit import default_timer as timer

from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser import PdfParser
from app.core.rag.deepdoc.parser.figure_parser import vision_figure_parser_pdf_wrapper


class DeepDocPdfParser(PdfParser, DocumentParser):
    def parse(self, ctx):
        sections, tables = self(
            ctx.filename if not ctx.binary else ctx.binary,
            from_page=ctx.from_page,
            to_page=ctx.to_page,
            callback=ctx.callback,
        )
        tables = vision_figure_parser_pdf_wrapper(
            tbls=tables,
            callback=ctx.callback,
            vision_model=ctx.vision_model,
            **ctx.kwargs,
        )
        return sections, tables, self

    def __call__(self, filename, binary=None, from_page=0,
                 to_page=100000, zoomin=3, callback=None, separate_tables_figures=False):
        start = timer()
        first_start = start
        callback(msg="OCR started")
        self.__images__(
            filename if not binary else binary,
            zoomin,
            from_page,
            to_page,
            callback,
        )
        callback(msg="OCR finished ({:.2f}s)".format(timer() - start))
        logging.info("OCR({}~{}): {:.2f}s".format(from_page, to_page, timer() - start))

        start = timer()
        self._layouts_rec(zoomin)
        callback(0.63, "Layout analysis ({:.2f}s)".format(timer() - start))

        start = timer()
        self._table_transformer_job(zoomin)
        callback(0.65, "Table analysis ({:.2f}s)".format(timer() - start))

        start = timer()
        self._text_merge(zoomin=zoomin)
        callback(0.67, "Text merged ({:.2f}s)".format(timer() - start))

        if separate_tables_figures:
            tables, figures = self._extract_table_figure(True, zoomin, True, True, True)
            self._concat_downward()
            logging.info("layouts cost: {}s".format(timer() - first_start))
            return [(box["text"], self._line_tag(box, zoomin)) for box in self.boxes], tables, figures

        tables = self._extract_table_figure(True, zoomin, True, True)
        self._naive_vertical_merge()
        self._concat_downward()
        self._final_reading_order_merge()
        logging.info("layouts cost: {}s".format(timer() - first_start))
        return [(box["text"], self._line_tag(box, zoomin)) for box in self.boxes], tables
