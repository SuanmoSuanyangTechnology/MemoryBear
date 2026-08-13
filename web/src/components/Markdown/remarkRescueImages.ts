/*
 * @Author: ZhaoYing
 * @Date: 2026-08-13 11:30:00
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-13 11:30:00
 */
/**
 * remark plugin: rescue embedded markdown images from "plain text" nodes.
 *
 * react-markdown (remark + micromark) correctly parses `![alt](url)` when the
 * surrounding AST is pure Markdown paragraph context. It does NOT do so when:
 *
 *   1. The image pattern is HTML-entity encoded:
 *      `!&#91;alt&#93;&#40;http://localhost:5173/api/files/xxx&#41;`
 *      — produced by HTML serialisers on the backend before Markdown is set.
 *   2. The chunk lives inside a raw-HTML block (`<p>![](url)</p>`) that is
 *      forwarded verbatim by `rehypeRaw` — Markdown inside HTML blocks is
 *      intentionally not parsed by the CommonMark spec, so the string shows
 *      as plain text.
 *   3. Markdown-escape backslashes are present: `\!\[alt\]\(url\)` (common
 *      when content is round-tripped through WYSIWYG editors).
 *
 * This plugin runs after the standard remark parse pass. It walks every
 * `text`, `html`, `code` (inline only, never fenced code blocks), and
 * `inlineCode` mdast node, runs a tolerant regex over the literal value to
 * find anything shaped like `![?alt?](?url "?title?")` — after first
 * normalising HTML entities and backslash escapes — then replaces that
 * portion of the original text node with:
 *
 *   [text_before, image_mdast_node, text_after]
 *
 * Children of `paragraph`, `heading`, `listItem`, `tableCell`, `blockquote`,
 * and `root` are processed; any node whose direct `children` array is not
 * exposed is left untouched for safety.
 *
 * The regex accepts the URL portion unquoted (`(url)`), single-quoted
 * (`(url 't')`) or double-quoted (`(url "t")`) and tolerates any amount of
 * whitespace around URL / title.  Balanced parens inside the URL are also
 * tolerated (Wikipedia links `[Text](https://en.wikipedia.org/wiki/Foo_(bar))`
 * were historically broken by naïve regex).
 */

import type { Plugin } from 'unified';
import type { Root, Text, Image, PhrasingContent } from 'mdast';
import { visit, SKIP, CONTINUE } from 'unist-util-visit';

/** HTML entities known to show up in Markdown special chars after a HTML round-trip */
const HTML_ENTITY_REPLACEMENTS: Array<[RegExp, string]> = [
  [/&#91;/gi, '['],
  [/&#93;/gi, ']'],
  [/&#40;/gi, '('],
  [/&#41;/gi, ')'],
  [/&#33;/gi, '!'],
  [/&quot;/gi, '"'],
  [/&#34;/gi, '"'],
  [/&#39;/gi, "'"],
  [/&apos;/gi, "'"],
  [/&amp;/gi, '&'],
  [/&lt;/gi, '<'],
  [/&gt;/gi, '>'],
  [/&#123;/gi, '{'],
  [/&#125;/gi, '}'],
];

/** Decode HTML entities and the subset of `\char` escapes that would hide a md image token */
const normalizeTokenizedText = (raw: string): string => {
  let out = raw;
  for (const [pattern, replacement] of HTML_ENTITY_REPLACEMENTS) {
    out = out.replace(pattern, replacement);
  }
  // Remove backslash-escapes of the 6 chars used by markdown image syntax
  // ONLY when the backslash is not itself escaped.  `\\!` stays `\\!`, `\!` becomes `!`.
  out = out.replace(/(^|[^\\])\\([!()[\]])/g, '$1$2');
  return out;
};

/**
 * Locate the matching close-paren for a markdown image `(url "title")` payload
 * starting at position `openAt` within `s`.  Returns the index of the matching
 * `)` after balancing nested parens, or `-1` if no balanced close exists.
 */
const findBalancedCloseParen = (s: string, openAt: number): number => {
  let depth = 0;
  for (let i = openAt; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 40 /* ( */) {
      depth += 1;
    } else if (c === 41 /* ) */) {
      depth -= 1;
      if (depth === 0) return i;
      if (depth < 0) return -1;
    } else if (c === 34 /* " */ || c === 39 /* ' */) {
      // Skip quoted title region; balanced parens inside quotes do not count.
      const quote = s[i];
      let j = i + 1;
      while (j < s.length && s[j] !== quote) {
        if (s[j] === '\\' && j + 1 < s.length) j += 2;
        else j += 1;
      }
      i = j; // if j overflows, for-loop exits naturally
    }
  }
  return -1;
};

interface ImageMatch {
  start: number;            // index in NORMALIZED string of the leading `!`
  end: number;              // index in NORMALIZED string just after the close `)`
  alt: string;              // content inside `[alt]` (maybe empty)
  url: string;              // content inside the url-part of `(url "title")`
  title: string | null;     // content inside quotes, if any
  matchedRaw: string;       // exact slice captured
}

/**
 * Find one markdown image token in `normalized` starting no earlier than `from`.
 * Implemented manually so balanced parens / HTML entities don't confuse regex.
 */
const findNextImage = (normalized: string, from: number): ImageMatch | null => {
  for (let i = from; i < normalized.length - 1; i++) {
    // Must be either start-of-string, previous char NOT an unescaped `!`
    // (prevents matching `!![](...)` twice on the same bracket set) and
    // previous char must not be a backslash that's itself unescaped.
    if (normalized.charCodeAt(i) !== 33 /* ! */) continue;
    if (normalized.charCodeAt(i + 1) !== 91 /* [ */) continue;
    // Walk to matching `]`, accepting escaped `\]` inside.
    let altEnd = -1;
    let j = i + 2;
    while (j < normalized.length) {
      const c = normalized[j];
      if (c === '\\' && j + 1 < normalized.length) {
        j += 2;
        continue;
      }
      if (c === ']') {
        altEnd = j;
        break;
      }
      j += 1;
    }
    if (altEnd === -1) continue;
    const alt = normalized.slice(i + 2, altEnd);
    const nextParen = normalized.charCodeAt(altEnd + 1);
    if (nextParen !== 40 /* ( */) continue;
    const closeParen = findBalancedCloseParen(normalized, altEnd + 1);
    if (closeParen === -1) continue;
    const payload = normalized.slice(altEnd + 2, closeParen);
    // Separate URL from optional quoted title.
    let url = '';
    let title: string | null = null;
    const trimmedPayload = payload.trim();
    const lastSpaceRun = trimmedPayload.search(/\s+(?=(?:"[^"]*"|'[^']*')\s*$)/);
    if (lastSpaceRun !== -1) {
      url = trimmedPayload.slice(0, lastSpaceRun).trim();
      const titlePart = trimmedPayload.slice(lastSpaceRun).trim();
      if (titlePart.length >= 2) {
        const firstQuote = titlePart[0];
        const lastQuote = titlePart[titlePart.length - 1];
        if ((firstQuote === '"' || firstQuote === "'") && firstQuote === lastQuote) {
          title = titlePart.slice(1, -1);
        } else {
          url = trimmedPayload;
        }
      } else {
        url = trimmedPayload;
      }
    } else {
      url = trimmedPayload;
    }
    if (!url) continue;
    return {
      start: i,
      end: closeParen + 1,
      alt,
      url,
      title,
      matchedRaw: normalized.slice(i, closeParen + 1),
    };
  }
  return null;
};

const CONTAINER_TYPES = new Set<string>([
  'root',
  'paragraph',
  'heading',
  'listItem',
  'tableCell',
  'blockquote',
  'strong',
  'emphasis',
  'delete',
  'footnote',
  'link',
  'linkReference',
]);

const makeImageNode = (match: ImageMatch): Image => ({
  type: 'image',
  url: match.url,
  alt: match.alt,
  title: match.title ?? undefined,
});

const makeTextNode = (value: string): Text => ({ type: 'text', value });

const remarkRescueImages: Plugin<Array<unknown>, Root> = () => {
  return (tree: Root) => {
    visit(tree, (node, index, parent) => {
      if (!parent || typeof index !== 'number') return CONTINUE;
      if (!CONTAINER_TYPES.has(parent.type)) return CONTINUE;

      // Extract string payload to search.  `html` and `text` are the two
      // nodes where image syntax can hide after a bad HTML round-trip.  We
      // deliberately skip `code`/`inlineCode` so backtick-wrapped examples
      // like `` `![](url)` `` are preserved as literal code, not turned into
      // pictures.
      let originalText: string | null = null;
      if (node.type === 'text') {
        originalText = (node as Text).value;
      } else if (node.type === 'html') {
        originalText = (node as unknown as { value: string }).value;
      }
      if (originalText === null || originalText.length === 0) return CONTINUE;

      const normalized = normalizeTokenizedText(originalText);
      if (!/!\[/.test(normalized) && !/!&#/.test(originalText)) {
        // Fast path: nothing even looks like an image — skip.
        return CONTINUE;
      }

      const matches: ImageMatch[] = [];
      {
        let cursor = 0;
        let m: ImageMatch | null;
        while ((m = findNextImage(normalized, cursor)) !== null) {
          matches.push(m);
          cursor = m.end;
        }
      }
      if (matches.length === 0) return CONTINUE;

      const replacement: PhrasingContent[] = [];
      let head = 0;
      for (const m of matches) {
        if (m.start > head) replacement.push(makeTextNode(originalText.slice(head, findCorrespondingStart(originalText, normalized, head, m.start))));
        replacement.push(makeImageNode(m));
        head = findCorrespondingEnd(originalText, normalized, m.end);
      }
      if (head < originalText.length) replacement.push(makeTextNode(originalText.slice(head)));

      // If we only produced pure-empty text nodes plus a single image — replace
      // wholesale.  Always mutate children via splice so ancestor bookkeeping
      // stays consistent with `unist-util-visit` (we return SKIP afterwards).
      parent.children.splice(index, 1, ...replacement as Array<typeof parent.children[number]>);
      return SKIP;
    });
  };
};

/**
 * Align a start-offset in the `normalized` string back to the offset in the
 * `original` string it corresponds to.  Normalization is monotonic (it only
 * removes characters — the single-char entity replacements keep char counts
 * equal, only backslash `\X` → `X` shrinks the string by 1 per match).  We
 * therefore replay `normalizeTokenizedText` one codepoint at a time until
 * `consumedNormalized === targetNormOffset` and return the original offset.
 */
function findCorrespondingStart(original: string, normalized: string, origStart: number, targetNormOffset: number): number {
  let i = origStart;
  let n = 0;
  // Walk `original` one token at a time (HTML entity / backslash escape / plain
  // character) and count how many characters `normalized` would produce for
  // that token.  This lets us translate a `normalized` string offset back to
  // the corresponding offset in `original` even after HTML entities / escapes
  // have collapsed the character count.
  void normalized;
  while (i < original.length) {
    // Match HTML entities at position `i` first (they all start with `&`).
    if (original.charCodeAt(i) === 38 /* & */) {
      // Find entity end `;`.
      let j = i + 1;
      while (j < original.length && original.charCodeAt(j) !== 59 /* ; */ && j - i < 10) j++;
      if (original.charCodeAt(j) === 59 /* ; */) {
        // Was this entity replaced by a single character?
        const slice = original.slice(i, j + 1);
        const normalizedSlice = normalizeTokenizedText(slice);
        if (normalizedSlice.length === 0) {
          i = j + 1;
          continue;
        }
        // Count all normalized chars produced.
        for (let k = 0; k < normalizedSlice.length; k++) {
          if (n === targetNormOffset) return i;
          n += 1;
        }
        i = j + 1;
        continue;
      }
    }
    // Handle `\X` backslash escape pair → 1 normalized char (consumes 2 orig bytes)
    if (original.charCodeAt(i) === 92 /* \ */ && i + 1 < original.length) {
      const next = original[i + 1];
      if ('!()[]'.indexOf(next) !== -1) {
        if (n === targetNormOffset) return i;
        n += 1;
        i += 2;
        continue;
      }
    }
    if (n === targetNormOffset) return i;
    n += 1;
    i += 1;
  }
  return original.length;
}

/** Same logic as `findCorrespondingStart` but returns the end-exclusive offset */
function findCorrespondingEnd(original: string, normalized: string, targetNormOffset: number): number {
  return findCorrespondingStart(original, normalized, 0, targetNormOffset);
}

export default remarkRescueImages;
