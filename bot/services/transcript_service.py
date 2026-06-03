"""
Relay Bot — Transcript Service
Generates dark-mode operational PDF transcripts from ticket channels.
Scoped by user_id + source_guild_id for community isolation.

Design philosophy:
    - Strict vertical, deterministic layout. Every block resets X to l_margin.
    - All widths derived from effective page width (epw).
    - Dark charcoal background with semantic message-type accents.
    - Visual hierarchy optimized for moderation review and investigations,
      not casual reading.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import discord
from fpdf import FPDF

log = logging.getLogger("relay.transcript")

# ── Storage ───────────────────────────────────────────────────────────────
_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_TRANSCRIPT_DIR = _ROOT_DIR / "data" / "transcripts"
TRANSCRIPT_DIR = Path(os.getenv("TRANSCRIPT_DIR", str(_DEFAULT_TRANSCRIPT_DIR)))
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

# ── Font Resolution ───────────────────────────────────────────────────────
_DEFAULT_FONT_DIR = _ROOT_DIR / "data" / "fonts"
FONT_DIR = Path(os.getenv("FONT_DIR", str(_DEFAULT_FONT_DIR)))
_FONT_CANDIDATES = [
    FONT_DIR,
    _ROOT_DIR / "fonts",
]


def _find_font(filename: str) -> str | None:
    for base in _FONT_CANDIDATES:
        p = base / filename
        if p.exists():
            return str(p)
    return None


def _ensure_fonts(pdf: FPDF) -> str:
    """Register bundled DejaVu fonts; fall back to Helvetica only if absent."""
    regular = _find_font("DejaVuSans.ttf")
    bold = _find_font("DejaVuSans-Bold.ttf")
    italic = _find_font("DejaVuSans-Oblique.ttf")

    if not (regular and bold):
        log.warning(
            "Bundled DejaVu fonts missing in data/fonts/; transcript Unicode "
            "fidelity will degrade. Place DejaVuSans.ttf and DejaVuSans-Bold.ttf there."
        )
        return "Helvetica"

    try:
        pdf.add_font("DejaVu", "", regular, uni=True)
        pdf.add_font("DejaVu", "B", bold, uni=True)
        if italic:
            pdf.add_font("DejaVu", "I", italic, uni=True)

        # Optional symbol fallback for emoji/dingbat coverage.
        symbols = _find_font("NotoSansSymbols2-Regular.ttf")
        if symbols and hasattr(pdf, "set_fallback_fonts"):
            try:
                pdf.add_font("NotoSym", "", symbols, uni=True)
                pdf.set_fallback_fonts(["NotoSym"])
            except Exception:
                pass
        return "DejaVu"
    except Exception as e:
        log.warning(f"DejaVu registration failed ({e}); falling back to Helvetica.")
        return "Helvetica"


# ── Text Sanitization ─────────────────────────────────────────────────────
_MAX_TOKEN_LEN = 80


def _wrap_long_tokens(text: str, max_len: int = _MAX_TOKEN_LEN) -> str:
    """Insert soft break points into very long unbroken tokens (URLs, IDs)."""
    if not text:
        return text
    out = []
    for token in text.split(" "):
        if len(token) > max_len:
            chunks = [token[i:i + max_len] for i in range(0, len(token), max_len)]
            out.append(" ".join(chunks))
        else:
            out.append(token)
    return " ".join(out)


def _strip_markdown(text: str) -> str:
    """Strip Discord markdown for clean transcript readability."""
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"\*", "", text)
    text = re.sub(r"__", "", text)
    text = re.sub(r"~~", "", text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"\n\s*\n", "\n", text)
    return text.strip()


# ── Dark Mode Color Palette (RGB) ─────────────────────────────────────────
PAGE_BG          = (30, 32, 38)     # dark charcoal (not pure black)
TEXT_PRIMARY     = (220, 222, 226)  # warm off-white body text
TEXT_MUTED       = (150, 154, 162)  # subdued gray for IDs, timestamps
TEXT_DIM         = (110, 114, 122)  # very muted (separators, edited tags)

ACCENT_STAFF     = (120, 180, 255)  # soft blue   — staff replies
ACCENT_USER      = (140, 220, 160)  # soft green  — user replies
ACCENT_ANON      = (200, 160, 240)  # lavender    — anonymous replies
ACCENT_SYSTEM    = (255, 200, 130)  # amber       — Relay/system notices
ACCENT_STAFFCHAT = (180, 184, 192)  # neutral gray — staff side chatter / commands

EMBED_BG         = (42, 45, 52)     # slightly lighter than page bg
EMBED_BORDER     = (80, 90, 110)    # subtle bluish border

HEADER_BG        = (40, 44, 52)     # transcript header band
HEADER_ACCENT    = (255, 200, 130)  # amber title accent

SEPARATOR        = (60, 64, 72)     # block separator color


# ── Message Type Classification ───────────────────────────────────────────
class MsgType:
    USER = "user"          # User's DM relayed into staff channel
    STAFF = "staff"        # Staff reply (visible)
    ANONYMOUS = "anon"     # Staff reply (anonymous)
    SYSTEM = "system"      # Relay system embed (open/close/claim/etc.)
    STAFF_CHAT = "chat"    # Side chatter / commands inside staff channel


def _get_staff_role_info(msg: discord.Message) -> tuple[str, tuple[int, int, int]]:
    """
    Resolve staff member's top role name and color for transcript attribution.
    Returns (role_label, role_color_rgb).
    Falls back to ("STAFF", ACCENT_STAFF) if role resolution fails.
    """
    author = msg.author
    if not isinstance(author, discord.Member):
        return "STAFF", ACCENT_STAFF

    # Get top role (highest position in hierarchy)
    top_role = author.top_role
    if top_role is None or top_role.is_default():
        return "STAFF", ACCENT_STAFF

    # Use role name (truncated if very long)
    role_name = top_role.name[:20] + "..." if len(top_role.name) > 20 else top_role.name
    role_name = role_name.upper()

    # Get role color (convert discord.Color to RGB tuple)
    role_color = top_role.color
    if role_color is None or role_color.value == 0:
        return role_name, ACCENT_STAFF

    # discord.Color.value is an integer, convert to RGB
    rgb = role_color.to_rgb()
    return role_name, rgb


def _classify(msg: discord.Message) -> str:
    """
    Categorize a message based on relay_service formatting conventions:
      - Bot text starting with `__username:__`     → USER (DM relay)
      - Bot text containing `📨 *Anonymous reply sent*` → ANONYMOUS
      - Bot text containing `📨 *Reply sent*`           → STAFF
      - Bot embed messages (no plain content)     → SYSTEM
      - Non-bot human messages                    → STAFF_CHAT
    """
    if msg.author.bot:
        content = msg.content or ""
        if content.startswith("__") and ":__" in content.split("\n", 1)[0]:
            return MsgType.USER
        if "📨 *Anonymous reply sent*" in content:
            return MsgType.ANONYMOUS
        if "📨 *Reply sent*" in content:
            return MsgType.STAFF
        if msg.embeds and not content.strip():
            return MsgType.SYSTEM
        return MsgType.SYSTEM
    return MsgType.STAFF_CHAT


_TYPE_LABEL = {
    MsgType.USER: "USER",
    MsgType.STAFF: "STAFF",
    MsgType.ANONYMOUS: "ANONYMOUS",
    MsgType.SYSTEM: "SYSTEM",
    MsgType.STAFF_CHAT: "STAFF",
}

_TYPE_COLOR = {
    MsgType.USER: ACCENT_USER,
    MsgType.STAFF: ACCENT_STAFF,
    MsgType.ANONYMOUS: ACCENT_ANON,
    MsgType.SYSTEM: ACCENT_SYSTEM,
    MsgType.STAFF_CHAT: ACCENT_STAFFCHAT,
}


# ── PDF Subclass with Dark Background ─────────────────────────────────────
class _TranscriptPDF(FPDF):
    font_family: str = "Helvetica"

    def header(self):  # noqa: D401 - fpdf hook
        # Paint dark background across full page on every page break.
        self.set_fill_color(*PAGE_BG)
        self.rect(0, 0, self.w, self.h, "F")

    def footer(self):  # noqa: D401 - fpdf hook
        self.set_y(-12)
        self.set_x(self.l_margin)
        self.set_font(self.font_family, "", 8)
        self.set_text_color(*TEXT_DIM)
        self.cell(self.w - self.l_margin - self.r_margin, 6,
                  f"Relay Transcript  ·  Page {self.page_no()}", align="C")


# ── Transcript Generation ─────────────────────────────────────────────────
async def generate_transcript(
    channel: discord.TextChannel,
    ticket: dict,
    closed_by: discord.Member | None = None,
) -> str:
    """
    Generate a dark-mode operational transcript PDF.
    Returns absolute file path of the generated PDF.
    """
    ticket_id = ticket["id"]
    user_id = ticket["user_id"]
    source_guild_id = ticket.get("source_guild_id")
    guild_id = ticket["guild_id"]
    community_number = ticket.get("community_ticket_number") or ticket_id

    # Collect messages in chronological order
    messages: list[discord.Message] = []
    async for msg in channel.history(limit=None, oldest_first=True):
        messages.append(msg)

    # ── PDF Setup ───────────────────────────────────────
    pdf = _TranscriptPDF()
    pdf.set_margins(left=14, top=14, right=14)
    family = _ensure_fonts(pdf)
    pdf.font_family = family
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    epw = pdf.w - pdf.l_margin - pdf.r_margin

    # ── Helper: Sanitization ────────────────────────────
    def safe(text: str) -> str:
        if not text:
            return ""
        if family == "Helvetica":
            text = text.encode("latin-1", "replace").decode("latin-1")
        return _wrap_long_tokens(text)

    # ── Helper: Reset cursor to clean left-margin state ─
    def reset_x() -> None:
        pdf.set_x(pdf.l_margin)

    # ── Helper: Bounded multi_cell with deterministic flow ─
    def render_text(text: str, *, width: float | None = None,
                    line_h: float = 5.0, fill: bool = False) -> None:
        """Render text with multi_cell at left margin; cursor returns to
        left margin after rendering. Width clamped to epw."""
        reset_x()
        w = width if width is not None else epw
        if w <= 0:
            w = epw
        try:
            pdf.multi_cell(w, line_h, safe(text), border=0, align="L",
                           fill=fill, new_x="LMARGIN", new_y="NEXT")
        except Exception as e:
            log.warning(f"Skipping unrenderable transcript line: {e}")
            try:
                pdf.multi_cell(w, line_h, "[unrenderable line]",
                               new_x="LMARGIN", new_y="NEXT")
            except Exception:
                pass
        reset_x()

    # ══════════════════════════════════════════════════════
    # Header Band
    # ══════════════════════════════════════════════════════
    _render_header(
        pdf, family, epw,
        community_number=community_number,
        ticket_id=ticket_id,
        channel=channel,
        guild_id=guild_id,
        source_guild_id=source_guild_id,
        user_id=user_id,
        msg_count=len(messages),
        closed_by=closed_by,
        safe=safe,
    )

    # ══════════════════════════════════════════════════════
    # Conversation
    # ══════════════════════════════════════════════════════
    for msg in messages:
        _render_message(pdf, family, epw, msg, render_text, safe, reset_x)

    # ══════════════════════════════════════════════════════
    # Investigation Threads (appended chronologically)
    # ══════════════════════════════════════════════════════
    threads = await _collect_ticket_threads(channel)
    for thread in threads:
        try:
            await _render_thread_section(
                pdf, family, epw, thread,
                render_text=render_text, safe=safe, reset_x=reset_x,
            )
        except Exception as e:
            log.warning(
                f"Skipping thread {getattr(thread, 'id', '?')} during transcript: {e}"
            )
            continue

    # ── Output ──────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"transcript-{ticket_id}-{user_id}-{timestamp}.pdf"
    filepath = TRANSCRIPT_DIR / filename
    pdf.output(str(filepath))
    return str(filepath)


# ══════════════════════════════════════════════════════════
# Header Block
# ══════════════════════════════════════════════════════════
def _render_header(
    pdf: FPDF, family: str, epw: float, *,
    community_number, ticket_id: int,
    channel: discord.TextChannel,
    guild_id: int, source_guild_id: int | None,
    user_id: int, msg_count: int,
    closed_by: discord.Member | None,
    safe,
) -> None:
    pdf.set_x(pdf.l_margin)
    band_top = pdf.get_y()

    # Band background
    pdf.set_fill_color(*HEADER_BG)
    pdf.rect(pdf.l_margin, band_top, epw, 26, "F")

    # Title
    pdf.set_xy(pdf.l_margin + 4, band_top + 3)
    pdf.set_font(family, "B", 16)
    pdf.set_text_color(*HEADER_ACCENT)
    pdf.cell(epw - 8, 8, safe(f"Relay Transcript  ·  Ticket #{community_number}"),
             new_x="LMARGIN", new_y="NEXT")

    # Subtitle row
    pdf.set_x(pdf.l_margin + 4)
    pdf.set_font(family, "", 9)
    pdf.set_text_color(*TEXT_MUTED)
    closer = closed_by.display_name if closed_by else "—"
    closed_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    sub = f"#{channel.name}  ·  Closed by {closer}  ·  {closed_at}"
    pdf.cell(epw - 8, 5, safe(sub), new_x="LMARGIN", new_y="NEXT")

    # Move below band
    pdf.set_y(band_top + 28)
    pdf.set_x(pdf.l_margin)

    # Metadata grid (two-column-ish via flat lines)
    pdf.set_font(family, "", 9)
    pdf.set_text_color(*TEXT_PRIMARY)

    def meta(label: str, value: str) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font(family, "B", 9)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(34, 5, safe(label))
        pdf.set_font(family, "", 9)
        pdf.set_text_color(*TEXT_PRIMARY)
        pdf.multi_cell(epw - 34, 5, safe(value),
                       new_x="LMARGIN", new_y="NEXT")

    meta("Ticket ID", str(ticket_id))
    meta("Channel", channel.name)
    meta("Guild", f"{channel.guild.name}  (ID {guild_id})")
    if source_guild_id and source_guild_id != guild_id:
        meta("Source Guild", str(source_guild_id))
    meta("User ID", str(user_id))
    meta("Messages", str(msg_count))

    # Divider
    pdf.ln(2)
    pdf.set_draw_color(*SEPARATOR)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.l_margin + epw, y)
    pdf.ln(4)
    pdf.set_x(pdf.l_margin)


# ══════════════════════════════════════════════════════════
# Message Block
# ══════════════════════════════════════════════════════════
def _render_message(
    pdf: FPDF, family: str, epw: float,
    msg: discord.Message, render_text, safe, reset_x,
) -> None:
    """Render a single chronological message block with deterministic flow."""
    reset_x()
    mtype = _classify(msg)
    color = _TYPE_COLOR[mtype]
    label = _TYPE_LABEL[mtype]

    # Resolve staff role attribution for hierarchy visibility
    if mtype in (MsgType.STAFF, MsgType.STAFF_CHAT):
        role_label, role_color = _get_staff_role_info(msg)
        label = role_label
        color = role_color

    ts = msg.created_at.astimezone().strftime("%Y-%m-%d  %H:%M:%S")
    author = msg.author
    author_name = author.display_name if isinstance(author, discord.Member) else author.name
    author_id = author.id

    # Resolve a clean display name from relay-formatted bot text when
    # possible (e.g. "__emma0958:__\nbody…" → "emma0958").
    relayed_name = _extract_relayed_username(msg, mtype)
    display_name = relayed_name or author_name

    # ── Accent stripe (left edge of block) ──────────────
    block_top = pdf.get_y()
    pdf.set_fill_color(*color)
    pdf.rect(pdf.l_margin, block_top, 1.4, 0.1, "F")  # placeholder, redrawn after height known

    # ── Header line: type tag + name + timestamp ────────
    pdf.set_x(pdf.l_margin + 4)
    pdf.set_font(family, "B", 9)
    pdf.set_text_color(*color)
    pdf.cell(0, 5, safe(f"[{label}]"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 4)
    pdf.set_font(family, "B", 10)
    pdf.set_text_color(*TEXT_PRIMARY)
    pdf.cell(0, 5, safe(f"{display_name}"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 4)
    pdf.set_font(family, "", 8)
    pdf.set_text_color(*TEXT_MUTED)
    sub = f"{ts}   ·   ID {author_id}"
    if msg.edited_at:
        sub += "   ·   edited"
    pdf.cell(0, 4.5, safe(sub), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    # ── Body content ────────────────────────────────────
    body = _extract_body(msg, mtype)
    if body:
        pdf.set_x(pdf.l_margin + 4)
        pdf.set_font(family, "", 9.5)
        pdf.set_text_color(*TEXT_PRIMARY)
        body_w = epw - 4
        try:
            pdf.multi_cell(body_w, 5, safe(body),
                           new_x="LMARGIN", new_y="NEXT")
        except Exception as e:
            log.warning(f"Body render failed: {e}")
        reset_x()

    # ── Embeds ──────────────────────────────────────────
    for emb in msg.embeds:
        _render_embed(pdf, family, epw, emb, safe, reset_x)

    # ── Attachments ─────────────────────────────────────
    for att in msg.attachments:
        _render_attachment(pdf, family, epw, att, safe, reset_x)

    # ── Block accent stripe (final height) ──────────────
    block_bottom = pdf.get_y()
    if block_bottom > block_top:
        pdf.set_fill_color(*color)
        pdf.rect(pdf.l_margin, block_top, 1.4, block_bottom - block_top, "F")

    # ── Separator + spacing ─────────────────────────────
    pdf.ln(1.5)
    pdf.set_draw_color(*SEPARATOR)
    pdf.set_line_width(0.15)
    sep_y = pdf.get_y()
    pdf.line(pdf.l_margin + 4, sep_y, pdf.l_margin + epw, sep_y)
    pdf.ln(3)
    reset_x()


# ── Helpers: extract relayed username and clean body ──────
_RELAY_USER_RE = re.compile(r"^__([^_]+):__\s*\n?")


def _extract_relayed_username(msg: discord.Message, mtype: str) -> str | None:
    if mtype == MsgType.USER:
        m = _RELAY_USER_RE.match(msg.content or "")
        if m:
            return m.group(1).strip()
    return None


def _extract_body(msg: discord.Message, mtype: str) -> str:
    """Return cleaned body text, stripping relay-format prefixes."""
    content = msg.content or ""
    if mtype == MsgType.USER:
        content = _RELAY_USER_RE.sub("", content, count=1)
    elif mtype in (MsgType.STAFF, MsgType.ANONYMOUS):
        # Strip the "📨 *Reply sent*" / "📨 *Anonymous reply sent*" prefix line(s)
        content = re.sub(r"^📨 \*[^*]+\*\s*\n", "", content, count=1)
    return _strip_markdown(content)


# ══════════════════════════════════════════════════════════
# Embed Block (visually distinct boxed section)
# ══════════════════════════════════════════════════════════
def _render_embed(pdf: FPDF, family: str, epw: float,
                  emb: discord.Embed, safe, reset_x) -> None:
    title = emb.title or ""
    desc = emb.description or ""
    fields = emb.fields or []

    if not (title or desc or fields):
        return

    reset_x()
    pdf.set_x(pdf.l_margin + 4)

    # Container width slightly inset
    box_x = pdf.l_margin + 4
    box_w = epw - 4

    # Pre-render content into a temporary measurement: use a try-rect approach.
    # We'll draw box, then content, accepting that height is determined dynamically.
    content_top = pdf.get_y()

    # Inner padding
    inner_pad_x = 3
    inner_pad_y = 2
    inner_w = box_w - 2 * inner_pad_x

    # Render title / description / fields, tracking height
    pdf.set_xy(box_x + inner_pad_x, content_top + inner_pad_y)
    if title:
        pdf.set_font(family, "B", 9.5)
        pdf.set_text_color(*TEXT_PRIMARY)
        pdf.multi_cell(inner_w, 5, safe(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(box_x + inner_pad_x)

    if desc:
        pdf.set_font(family, "", 9)
        pdf.set_text_color(*TEXT_PRIMARY)
        pdf.multi_cell(inner_w, 4.8, safe(_strip_markdown(desc)),
                       new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(box_x + inner_pad_x)

    for field in fields:
        f_name = getattr(field, "name", "") or ""
        f_value = getattr(field, "value", "") or ""
        if f_name:
            pdf.set_font(family, "B", 8.5)
            pdf.set_text_color(*TEXT_MUTED)
            pdf.multi_cell(inner_w, 4.5, safe(f_name),
                           new_x="LMARGIN", new_y="NEXT")
            pdf.set_x(box_x + inner_pad_x)
        if f_value:
            pdf.set_font(family, "", 8.5)
            pdf.set_text_color(*TEXT_PRIMARY)
            pdf.multi_cell(inner_w, 4.5, safe(_strip_markdown(f_value)),
                           new_x="LMARGIN", new_y="NEXT")
            pdf.set_x(box_x + inner_pad_x)

    content_bottom = pdf.get_y() + inner_pad_y
    height = max(content_bottom - content_top, 6)

    # Now redraw the embed background BEHIND the rendered content.
    # We do this by saving a snapshot of the raw drawing layer is not feasible
    # in FPDF; instead, we paint the rect and then re-render content on top.
    # Simpler: draw rect first using a measured pre-pass would be cleaner,
    # but to avoid complexity we draw the rect *after* and then re-render.
    # → Pragmatic compromise: draw left-border accent stripe only (always
    # under content rendering, since FPDF draws in order). Fill is achieved
    # via a thin background-shade strategy applied below.

    # Draw a left-border accent + bottom rule for the embed block.
    pdf.set_draw_color(*EMBED_BORDER)
    pdf.set_line_width(0.5)
    pdf.line(box_x, content_top, box_x, content_top + height)
    pdf.set_line_width(0.15)
    pdf.set_draw_color(*SEPARATOR)
    pdf.line(box_x, content_top + height, box_x + box_w, content_top + height)

    pdf.ln(2)
    reset_x()


# ══════════════════════════════════════════════════════════
# Attachment Block
# ══════════════════════════════════════════════════════════
def _render_attachment(pdf: FPDF, family: str, epw: float,
                       att: discord.Attachment, safe, reset_x) -> None:
    reset_x()
    pdf.set_x(pdf.l_margin + 4)
    pdf.set_font(family, "B", 8.5)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.cell(0, 4.5, safe(f"📎 attachment"),
             new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 6)
    pdf.set_font(family, "", 9)
    pdf.set_text_color(*TEXT_PRIMARY)
    line1 = att.filename
    if att.size:
        line1 += f"   ·   {_format_size(att.size)}"
    pdf.multi_cell(epw - 6, 4.8, safe(line1),
                   new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 6)
    pdf.set_font(family, "", 8)
    pdf.set_text_color(*TEXT_DIM)
    pdf.multi_cell(epw - 6, 4.5, safe(att.url),
                   new_x="LMARGIN", new_y="NEXT")
    reset_x()


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n / 1:.1f} {unit}"
        n = n / 1024  # type: ignore
    return f"{n} B"


# ══════════════════════════════════════════════════════════
# Investigation Threads
# ══════════════════════════════════════════════════════════
async def _collect_ticket_threads(
    channel: discord.TextChannel,
) -> list[discord.Thread]:
    """
    Collect threads attached to the ticket channel only.
    Returns active + archived (public/private) threads sorted chronologically
    by creation time. Failures are isolated; never raise.
    """
    seen: dict[int, discord.Thread] = {}

    # Active threads cached on the channel
    try:
        for t in getattr(channel, "threads", []) or []:
            if t.parent_id == channel.id:
                seen[t.id] = t
    except Exception as e:
        log.warning(f"Could not enumerate active threads on #{channel.id}: {e}")

    # Archived public threads
    try:
        async for t in channel.archived_threads(limit=None):
            seen.setdefault(t.id, t)
    except Exception as e:
        log.warning(f"Could not fetch archived public threads on #{channel.id}: {e}")

    # Archived private threads (may be permission-restricted)
    try:
        async for t in channel.archived_threads(private=True, limit=None):
            seen.setdefault(t.id, t)
    except Exception as e:
        log.warning(f"Could not fetch archived private threads on #{channel.id}: {e}")

    threads = list(seen.values())
    threads.sort(key=lambda t: getattr(t, "created_at", None) or datetime.now(timezone.utc))
    return threads


async def _render_thread_section(
    pdf: FPDF, family: str, epw: float,
    thread: discord.Thread, *,
    render_text, safe, reset_x,
) -> None:
    """Render a clearly compartmentalized thread section."""
    # Collect thread messages defensively
    messages: list[discord.Message] = []
    try:
        async for msg in thread.history(limit=None, oldest_first=True):
            messages.append(msg)
    except discord.Forbidden:
        log.warning(f"No access to thread {thread.id} ({thread.name}); skipping body")
    except Exception as e:
        log.warning(f"Failed reading thread {thread.id}: {e}")

    # Resolve creator (best-effort, no extra API spam)
    creator_name = "Unknown"
    try:
        if thread.owner is not None:
            creator_name = thread.owner.display_name
        elif thread.owner_id:
            creator_name = f"User {thread.owner_id}"
    except Exception:
        pass

    archived = "Yes" if getattr(thread, "archived", False) else "No"
    created_ts = (
        thread.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        if getattr(thread, "created_at", None) else "—"
    )

    # ── Section opener band ────────────────────────────
    reset_x()
    pdf.ln(2)
    band_top = pdf.get_y()
    pdf.set_fill_color(*HEADER_BG)
    pdf.rect(pdf.l_margin, band_top, epw, 18, "F")

    pdf.set_xy(pdf.l_margin + 4, band_top + 2)
    pdf.set_font(family, "B", 11)
    pdf.set_text_color(*HEADER_ACCENT)
    pdf.cell(epw - 8, 6, safe(f"THREAD: {thread.name or 'Unnamed Thread'}"),
             new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 4)
    pdf.set_font(family, "", 9)
    pdf.set_text_color(*TEXT_MUTED)
    sub = (
        f"Created By: {creator_name}   ·   "
        f"Archived: {archived}   ·   "
        f"Created: {created_ts}"
    )
    pdf.cell(epw - 8, 5, safe(sub), new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(band_top + 20)
    reset_x()

    # ── Thread body ────────────────────────────────────
    if not messages:
        pdf.set_font(family, "I", 9)
        pdf.set_text_color(*TEXT_DIM)
        render_text("(no messages or thread inaccessible)")
    else:
        for msg in messages:
            try:
                _render_message(pdf, family, epw, msg, render_text, safe, reset_x)
            except Exception as e:
                log.warning(f"Skipping unrenderable thread message {msg.id}: {e}")
                continue

    # ── Section closer ─────────────────────────────────
    reset_x()
    pdf.ln(1)
    pdf.set_draw_color(*SEPARATOR)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.l_margin + epw, y)
    pdf.ln(1)

    pdf.set_font(family, "B", 9)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.set_x(pdf.l_margin)
    pdf.cell(epw, 5, safe(f"END THREAD   ·   ID {thread.id}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    reset_x()
