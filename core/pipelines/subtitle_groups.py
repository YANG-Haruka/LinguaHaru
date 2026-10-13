# Sentence groups for subtitle formats (SRT / VTT / ASS / LRC).
#
# Real captions split one sentence over 2-3 timed cues. Sent one id per cue, a
# model merges the next cue's half-sentence into the current one, every later id
# drifts, and the emptied slot gets an invented "[Music]" or a repeated line. So
# consecutive cues forming a sentence are sent as ONE unit and the translation is
# split back over those cues (split_across_cues), one piece per cue, timing kept.
import re
import unicodedata

from core.log_config import app_logger

# Sentence end: CJK full stop/!/? anywhere, Latin .!?… only before space/end
# (so "3.5" and "U.S.A" mid-word don't count), plus trailing closing quotes.
_SENT_END = re.compile(r'(?:[。！？]|[.!?…](?=[\s"”’)\]」』]|$))["”’)\]」』]*')
_SENT_END_AT_END = re.compile(_SENT_END.pattern + r"\s*$")
# A cue that is only a sound tag ("[Music]", "(music)", "♪") is its own group.
_TAG_ONLY = re.compile(r"^\s*(?:\[[^\]]*\]|\([^)]*\)|【[^】]*】|（[^）]*）|♪+)\s*$")
# Any sound/caption tag in the source; and the ones a model invents.
_ANY_TAG = re.compile(r"\[[^\]\n]*\]|\([^)\n]*\)|【[^】\n]*】|（[^）\n]*）|♪")
# Only SOUND tags count as invented: a translator's "[sic]" / "[1]" / "【声明】"
# is legit. A tag counts only when it is WHOLLY a sound label ("[Music]",
# "[upbeat music playing]", "[音乐]", "【掌声】") or a bare "♪".
_SOUND_WORD = (r"music|musique|musik|m[uú]sica|музыка|applause|laughter|laughs|laughing|"
               r"cheering|cheers|silence|noise|inaudible")
_SOUND_CJK = r"音乐|音樂|音楽|BGM|掌声|掌聲|拍手|笑声|笑聲|笑|欢呼|歡呼|歓声|沉默|噪音|음악|박수|웃음"
_FAB_TAG = re.compile(
    r"\[\s*(?:[^\W\d_]+\s+){0,2}(?:" + _SOUND_WORD + r")(?:\s+[^\W\d_]+){0,2}\s*\]"
    r"|[\[【]\s*(?:" + _SOUND_CJK + r")\s*[\]】]|♪+", re.I)
_PUNCT = set("。！？；，、：,.;:!?…")
_CLOSERS = set("\"”’)]」』）")
# CJK/kana/fullwidth + Thai, Lao, Burmese, Khmer (no spaces between words)
_NO_SPACE_SCRIPT = re.compile("[฀-໿က-႟ក-៿　-ヿ㐀-鿿豈-﫿＀-￯]")
PAUSE_S = 1.0              # a gap this long between cues ends the sentence group
# ponytail: hard cap per unit; a run that hits it without a sentence end falls
# back to one unit per cue (old behaviour).
MAX_GROUP_CHARS = 1000


def flat(value):
    """Cue text with its line markers turned into spaces (one line)."""
    return re.sub(r"\s+", " ", value.replace("␊", " ").replace("␍", " ")).strip()


def seconds(ts):
    """'01:02:03,456' / '1:02:03.45' / '02:03.456' -> seconds."""
    total = 0.0
    for part in ts.strip().replace(",", ".").split(":"):
        total = total * 60 + float(part)
    return total


def join_texts(parts):
    out = ""
    for p in parts:
        if out and p and not (_NO_SPACE_SCRIPT.match(out[-1]) and _NO_SPACE_SCRIPT.match(p[0])):
            out += " "
        out += p
    return out


def group_cues(items, info):
    """Split items (in play order) into sentence groups.

    info(item) -> (flat_text, start_s, end_s, solo, speaker). A run of cues is
    merged only when it closes on sentence punctuation; a run broken by a pause,
    a speaker/style change, a solo cue (markup, sound tag) or the size cap is
    emitted one cue per group, since there is no telling where its sentence ends
    (lyrics, unpunctuated auto-captions)."""
    groups, cur, cur_len = [], [], 0
    prev_end = prev_speaker = None

    def close(ended):
        nonlocal cur, cur_len
        if cur:
            groups.extend([cur] if ended else [[c] for c in cur])
        cur, cur_len = [], 0

    for item in items:
        text, start, end, solo, speaker = info(item)
        if solo or not text or _TAG_ONLY.match(text):
            close(False)
            groups.append([item])
            continue
        # overlap (start before the previous cue ends) = simultaneous lines
        if cur and (start - prev_end >= PAUSE_S or start < prev_end - 0.05
                    or speaker != prev_speaker
                    or cur_len + len(text) > MAX_GROUP_CHARS):
            close(False)
        cur.append(item)
        cur_len += len(text)
        prev_end, prev_speaker = end, speaker
        if _SENT_END_AT_END.search(text):
            close(True)
    close(False)
    return groups


def _sentence_anchors(text):
    return [m.end() for m in _SENT_END.finditer(text) if m.end() < len(text.rstrip())]


def _inside(text, i, open_ch, close_ch):
    return text.rfind(open_ch, 0, i) > text.rfind(close_ch, 0, i)


def _cut_cost(text, i, ideal, tol):
    """Cost of cutting text before index i (None = never cut here)."""
    a, b = text[i - 1], text[i]
    if unicodedata.category(b).startswith("M"):
        return None                      # never orphan a combining mark (Thai vowel/tone)
    if b in _PUNCT or b in _CLOSERS or _inside(text, i, "<", ">") or _inside(text, i, "{", "}"):
        return None                      # don't orphan punctuation / split a tag
    if b.isdigit() and (a.isdigit() or (a in ",." and i >= 2 and text[i - 2].isdigit())):
        return None                      # never split a number ("400,|000")
    if a in _PUNCT or a in _CLOSERS or (a.isspace() and i >= 2 and text[i - 2] in _PUNCT | _CLOSERS):
        penalty = 0                      # after a clause/sentence break: best
    elif a.isspace() or b.isspace():
        penalty = tol                    # word boundary
    elif _NO_SPACE_SCRIPT.match(a) or _NO_SPACE_SCRIPT.match(b):
        penalty = tol * 1.5              # CJK character boundary
    else:
        return None                      # middle of a Latin word
    return abs(i - ideal) + penalty


def split_across_cues(src_text, src_parts, translated):
    """Split a sentence group's translation into one piece per cue.

    Each cue's share follows its share of the SOURCE characters; when source and
    translation have the same number of sentence breaks, those breaks are used as
    fixed anchors (a cue that ends a source sentence ends exactly at the matching
    translated sentence), the rest is interpolated. Cut points snap to the nearest
    punctuation / word / CJK-character boundary, never inside a number or a
    <tag>/{tag}. Always returns len(src_parts) pieces, so timing stays one-to-one
    with the source cues."""
    n = len(src_parts)
    text = flat(translated)
    if n == 1:
        return [text]
    if len(text) < n:
        return [text] + [""] * (n - 1)

    # Source offsets where each cue (but the last) ends
    bounds, pos = [], 0
    for part in src_parts[:-1]:
        found = src_text.find(part, pos)
        pos = (found if found >= 0 else pos) + len(part)
        bounds.append(pos)

    src_a = [0] + _sentence_anchors(src_text) + [len(src_text)]
    dst_a = [0] + _sentence_anchors(text) + [len(text)]
    if len(src_a) != len(dst_a):
        src_a, dst_a = [0, len(src_text)], [0, len(text)]

    def to_dst(x):
        for k in range(len(src_a) - 1):
            if x <= src_a[k + 1] or k == len(src_a) - 2:
                span = max(src_a[k + 1] - src_a[k], 1)
                return dst_a[k] + (x - src_a[k]) / span * (dst_a[k + 1] - dst_a[k])

    tol = max(2.0, len(text) / (4 * n))
    cuts, prev = [], 0
    for k, b in enumerate(bounds, start=1):
        ideal = to_dst(b)
        lo, hi = prev + 1, len(text) - (n - k)
        scored = [(c, i) for i in range(lo, hi + 1)
                  if (c := _cut_cost(text, i, ideal, tol)) is not None]
        # No allowed cut (one long word): never cut mid-word; this cue stays
        # empty and the text carries on into the following cue(s).
        cut = min(scored)[1] if scored else prev
        cuts.append(cut)
        prev = cut
    return [text[a:b].strip() for a, b in zip([0] + cuts, cuts + [len(text)])]


def drop_fabricated_tags(src_text, translated):
    """Remove "[音乐]" / "[Music]" / "♪" the model added to a unit whose source
    has no sound tag (models fill a slot they emptied by shifting text). If that
    leaves nothing, the unit was pure fabrication -> keep the source text."""
    if not translated or _ANY_TAG.search(src_text) or not _FAB_TAG.search(translated):
        return translated
    cleaned = re.sub(r"[ \t]{2,}", " ", _FAB_TAG.sub("", translated)).strip()
    app_logger.warning(f"Subtitle: dropped invented sound tag from {translated!r}")
    return cleaned or src_text
