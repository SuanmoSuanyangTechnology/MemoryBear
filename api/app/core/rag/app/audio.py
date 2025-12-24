import os
import re
import tempfile

from app.core.rag.nlp import rag_tokenizer, tokenize


def chunk(filename, binary, lang, callback=None, seq2txt_mdl=None, **kwargs):
    doc = {"docnm_kwd": filename, "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))}
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])

    # is it English
    eng = lang.lower() == "english"  # is_english(sections)
    try:
        _, ext = os.path.splitext(filename)
        if not ext:
            raise RuntimeError("No extension detected.")

        if ext not in [".da", ".wave", ".wav", ".mp3", ".aac", ".flac", ".ogg", ".aiff", ".au", ".midi", ".wma", ".realaudio", ".vqf", ".oggvorbis", ".ape"]:
            raise RuntimeError(f"Extension {ext} is not supported yet.")

        tmp_path = ""
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmpf:
            tmpf.write(binary)
            tmpf.flush()
            tmp_path = os.path.abspath(tmpf.name)

        callback(0.1, "USE Sequence2Txt LLM to transcription the audio")
        ans, ans_num_tokens = seq2txt_mdl.transcription(tmp_path)
        callback(0.8, "Sequence2Txt LLM respond: %s ..." % ans[:32])

        tokenize(doc, ans, eng)
        return [doc]
    except Exception as e:
        callback(prog=-1, msg=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return []
