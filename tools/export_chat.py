"""Claude Code 대화 기록(JSONL)을 한글이 깨지지 않는 단일 HTML 파일로 내보낸다.

사용: .venv/Scripts/python tools/export_chat.py <세션.jsonl> <출력.html> [--mask 비밀값 ...]
- 사용자 메시지와 Claude 답변만 담는다(도구 호출·결과는 '작업 n건'으로 요약).
- 외부 파일·인터넷 없이 열리는 UTF-8 HTML 한 개로 저장한다.
- --mask 로 준 문자열(API 키 등)은 ******** 로 가린다.
"""
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
STRIP = [r"<system-reminder>.*?</system-reminder>", r"<local-command-caveat>.*?</local-command-caveat>",
         r"<local-command-stdout>.*?</local-command-stdout>", r"<task-notification>.*?</task-notification>",
         r"<command-message>.*?</command-message>", r"<command-args>\s*</command-args>"]


def clean_user(text):
    for p in STRIP:
        text = re.sub(p, "", text, flags=re.S)
    m = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    text = re.sub(r"<command-name>.*?</command-name>", "", text, flags=re.S)
    text = text.strip()
    if m and not text:
        name = m.group(1).strip()
        return None if name in ("/model", "/context") else f"`{name}` 명령 실행"
    return text or None


def agent_report(text):
    m = re.search(r"The report follows:\s*(.*?)</agent-message>", text, re.S)
    body = m.group(1) if m else text
    return "\n".join(ln[2:] if ln.startswith("  ") else ln for ln in body.strip("\n").split("\n")).strip()


# ---------- 간단한 마크다운 → HTML ----------
def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r'(?<!["=>])(https?://[^\s<)]+)', r'<a href="\1">\1</a>', s)
    return s


def md(text):
    out, lines, i = [], text.replace("\r\n", "\n").split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        if ln.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            rows = [ln]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(lines[j])
                j += 1
            cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
            t = "<div class='tbl'><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells(rows[0])) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(r)) + "</tr>" for r in rows[1:])
            out.append(t + "</tbody></table></div>")
            i = j
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            lv = min(6, len(m.group(1)) + 2)
            out.append(f"<h{lv}>{inline(m.group(2))}</h{lv}>")
            i += 1
            continue
        if re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            items = []
            while i < len(lines) and (re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]) or (lines[i].startswith("   ") and lines[i].strip())):
                if re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                    ind = len(lines[i]) - len(lines[i].lstrip())
                    items.append([ind, re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]), bool(re.match(r"^\s*\d+\.", lines[i]))])
                else:
                    items[-1][1] += " " + lines[i].strip()
                i += 1
            tag = "ol" if items[0][2] else "ul"
            out.append(f"<{tag}>" + "".join(f"<li class='l{min(ind // 2, 3)}'>{inline(t)}</li>" for ind, t, _ in items) + f"</{tag}>")
            continue
        if ln.startswith(">"):
            q = []
            while i < len(lines) and lines[i].startswith(">"):
                q.append(lines[i].lstrip("> "))
                i += 1
            out.append("<blockquote>" + "<br>".join(inline(x) for x in q) + "</blockquote>")
            continue
        if not ln.strip():
            i += 1
            continue
        para = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|```|\||\s*([-*]|\d+\.)\s|>)", lines[i]):
            para.append(lines[i])
            i += 1
        out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")
    return "\n".join(out)


def user_html(text):
    """사용자 글: 붙여 넣은 내용(<pasted_content>)은 인용 상자로."""
    parts = re.split(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", text, flags=re.S)
    out = []
    for k, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        esc = html.escape(part).replace("\n", "<br>")
        out.append(f"<div class='paste'><span>붙여 넣은 내용</span>{esc}</div>" if k % 2 else f"<p>{esc}</p>")
    return "\n".join(out)


def load(path):
    turns = []  # (역할, 시각, 내용, 작업 수)

    def add(role, ts, body):
        if turns and turns[-1][0] == role == "claude":
            turns[-1][2].append(body)
        else:
            turns.append([role, ts, [body], 0])

    for ln in open(path, encoding="utf-8"):
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if o.get("isSidechain"):
            continue
        ts = o.get("timestamp", "")
        t = o.get("type")
        if t == "attachment" and isinstance(o.get("attachment"), dict) and o["attachment"].get("type") == "queued_command":
            add("user", ts, ("작업 중 보낸 메시지", o["attachment"].get("prompt", "")))
        elif t == "user" and not o.get("isMeta"):
            c = o["message"]["content"]
            if isinstance(c, list):
                if any(b.get("type") == "tool_result" for b in c):
                    continue
                c = "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
            if "<agent-message" in c:
                add("agent", ts, agent_report(c))
                continue
            if c.strip().startswith("[Request interrupted"):
                add("note", ts, "사용자가 작업을 중단함")
                continue
            c = clean_user(c)
            if c:
                add("user", ts, c)
        elif t == "assistant":
            for b in o["message"]["content"]:
                if b.get("type") == "text" and b.get("text", "").strip():
                    add("claude", ts, b["text"])
                elif b.get("type") == "tool_use" and turns and turns[-1][0] == "claude":
                    turns[-1][3] += 1
                elif b.get("type") == "tool_use":
                    turns.append(["claude", ts, [], 1])
    return [t for t in turns if t[2] or t[3]]


def when(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST).strftime("%H:%M")
    except ValueError:
        return ""


CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1e2124;--sub:#5a636c;--line:#dfe3e7;--me:#e9f1fe;--me-line:#c7dafc;--agent:#fff7e6;--agent-line:#f3d79a;--code:#f1f3f5;--accent:#256ef4}
@media (prefers-color-scheme:dark){:root{--bg:#15181c;--card:#1d2126;--ink:#e6e9ec;--sub:#9aa4ae;--line:#30363d;--me:#1b2a44;--me-line:#2c4778;--agent:#2b2412;--agent-line:#5c4a1f;--code:#262b31;--accent:#6c9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Malgun Gothic","맑은 고딕","Apple SD Gothic Neo",sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:14px max(16px,4vw);z-index:2}
header h1{margin:0;font-size:19px}header p{margin:2px 0 0;color:var(--sub);font-size:13px}
main{max-width:980px;margin:0 auto;padding:20px 16px 60px;display:flex;flex-direction:column;gap:14px}
.t{display:flex;flex-direction:column;gap:4px;min-width:0}.t .who{font-size:12px;color:var(--sub);font-weight:700}
.b{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:0;overflow-wrap:anywhere}
.user{align-items:flex-end}.user .b{background:var(--me);border-color:var(--me-line);max-width:88%}
.agent .b{background:var(--agent);border-color:var(--agent-line)}
.note{align-items:center;color:var(--sub);font-size:13px}
.b p{margin:.4em 0}.b h3,.b h4,.b h5{margin:.9em 0 .3em}.b ul,.b ol{margin:.3em 0;padding-left:1.4em}
li.l1{margin-left:1.2em}li.l2{margin-left:2.4em}li.l3{margin-left:3.6em}
code{background:var(--code);border-radius:4px;padding:1px 5px;font:13px Consolas,"D2Coding",monospace}
pre{background:var(--code);border-radius:6px;padding:10px 12px;overflow-x:auto}pre code{padding:0;background:none}
.tbl{overflow-x:auto}table{border-collapse:collapse;margin:.5em 0;font-size:14px}th,td{border:1px solid var(--line);padding:5px 9px;text-align:left;vertical-align:top}th{background:var(--code)}
blockquote{margin:.5em 0;padding:4px 12px;border-left:3px solid var(--accent);color:var(--sub)}
.paste{border-left:3px solid var(--accent);background:var(--card);padding:8px 12px;margin:6px 0;border-radius:4px;font-size:14px}
.paste span{display:block;font-size:12px;color:var(--sub);font-weight:700;margin-bottom:4px}
.tools{font-size:12px;color:var(--sub)}a{color:var(--accent)}
"""


def render(turns, title, masks):
    rows = []
    for role, ts, bodies, ntool in turns:
        tm = when(ts)
        if role == "note":
            rows.append(f"<div class='t note'>— {html.escape(bodies[0])} ({tm}) —</div>")
            continue
        if role == "user":
            inner = []
            for b in bodies:
                if isinstance(b, tuple):
                    inner.append(f"<p class='tools'>{b[0]}</p>" + user_html(b[1]))
                else:
                    inner.append(user_html(b))
            rows.append(f"<div class='t user'><div class='who'>사용자 · {tm}</div><div class='b'>{''.join(inner)}</div></div>")
        elif role == "agent":
            rows.append(f"<div class='t agent'><div class='who'>검증 에이전트 보고 · {tm}</div><div class='b'>{md(bodies[0])}</div></div>")
        else:
            body = "\n".join(md(b) for b in bodies)
            tools = f"<p class='tools'>작업 {ntool}건 수행</p>" if ntool else ""
            rows.append(f"<div class='t claude'><div class='who'>Claude · {tm}</div><div class='b'>{body}{tools}</div></div>")
    doc = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><header><h1>{html.escape(title)}</h1>"
           f"<p>메시지 {sum(1 for t in turns if t[0] == 'user')}개 · 답변 {sum(1 for t in turns if t[0] == 'claude')}개 · "
           f"작업 기록(도구 실행 내용)은 건수로만 표시</p></header><main>{''.join(rows)}</main></body></html>")
    for m in masks:
        if m:
            doc = doc.replace(m, "********")
    return doc


def main():
    args = sys.argv[1:]
    masks = []
    if "--mask" in args:
        k = args.index("--mask")
        masks, args = args[k + 1:], args[:k]
    src, dst = Path(args[0]), Path(args[1])
    title = args[2] if len(args) > 2 else "WaveText 개발 대화 기록"
    turns = load(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render(turns, title, masks), encoding="utf-8")
    print(f"저장: {dst} ({dst.stat().st_size / 1024:.0f} KB, 대화 {len(turns)}개)")


if __name__ == "__main__":
    main()
