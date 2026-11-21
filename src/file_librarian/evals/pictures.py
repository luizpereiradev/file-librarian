"""The corpus's pictures with words in them (#100), drawn with ``evals/raster.py``.

Six kinds of image a Downloads folder holds and that OCR finds text in, from
"a picture that happens to contain words" to "a document that happens to be
an image": a flyer, a chat screenshot, a meme, a landscape photo with a small
caption, a photographed receipt and a scanned page. Every organisation and
place is invented; there are no people's names.

Each function takes the path to write; the same call writes the same bytes.
"""

from __future__ import annotations

from pathlib import Path

from file_librarian.evals.raster import CELL_H, CELL_W, RGB, Canvas, text_width

WHITE: RGB = (255, 255, 255)
BLACK: RGB = (20, 20, 20)
INK: RGB = (35, 35, 40)


def _outlined(canvas: Canvas, y: int, line: str, scale: int) -> None:
    """White capitals with a black outline, centred: meme lettering."""
    x = (canvas.width - text_width(line, scale)) // 2
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, 2), (2, 0), (-2, 0), (0, -2)):
        canvas.text(x + dx * scale // 2, y + dy * scale // 2, line, scale, BLACK)
    canvas.text(x, y, line, scale, WHITE)


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def flyer(path: Path) -> Path:
    """A community event flyer: a few big lines over shapes and colour."""
    c = Canvas(900, 1200)
    c.vgradient(0, 1200, (255, 166, 77), (230, 80, 120))
    c.circle(760, 160, 210, (255, 210, 120))
    c.circle(120, 1080, 260, (250, 120, 150))
    c.rounded(70, 300, 760, 300, 40, (255, 248, 235))
    c.centered(340, "FEIRA DE TROCAS", 7, (190, 40, 70))
    c.centered(450, "livros, discos e plantas", 4, INK)
    c.centered(690, "Sábado, 15 de novembro", 5, WHITE)
    c.centered(790, "9h às 13h", 6, WHITE)
    c.centered(900, "Praça das Acácias", 4, WHITE)
    c.centered(1110, "entrada gratuita", 3, (255, 240, 200))
    return c.save(path)


def chat(path: Path) -> Path:
    """A phone screenshot of a group chat: bubbles on both sides."""
    c = Canvas(750, 1334, (236, 229, 221))
    c.rect(0, 0, 750, 150, (0, 110, 95))
    c.circle(80, 95, 36, (200, 225, 220))
    c.text(140, 70, "Vizinhos Bloco B", 4, WHITE)
    c.text(140, 120, "12 participantes", 2, (200, 235, 228))
    c.rect(0, 1234, 750, 100, (245, 245, 245))
    c.rounded(30, 1254, 600, 60, 30, WHITE)
    c.text(60, 1270, "Mensagem", 3, (150, 150, 150))
    messages = (
        (False, "Bom dia! Vai faltar água amanhã?", "08:12"),
        (False, "O aviso diz das 8h às 12h", "08:13"),
        (True, "Obrigado pelo aviso!", "08:20"),
        (False, "Alguém sabe se a garagem também fica sem?", "08:31"),
        (True, "Acho que só a torre A", "08:33"),
        (False, "Vou encher uns baldes hoje então", "08:40"),
        (True, "Boa ideia", "08:41"),
    )
    y, scale = 190, 3
    for mine, text, time in messages:
        lines = _wrap(text, 24)
        w = max(text_width(line, scale) for line in lines) + 60
        h = len(lines) * (CELL_H * scale + 4) + 50
        x = 750 - 30 - w if mine else 30
        c.rounded(x, y, w, h, 18, (220, 248, 198) if mine else WHITE)
        for i, line in enumerate(lines):
            c.text(x + 24, y + 16 + i * (CELL_H * scale + 4), line, scale, INK)
        c.text(x + w - 24 - text_width(time, 2), y + h - 30, time, 2, (130, 130, 130))
        y += h + 26
    return c.save(path)


def meme(path: Path) -> Path:
    """A cartoon face with a caption above and below, in meme lettering."""
    c = Canvas(800, 800)
    c.vgradient(0, 800, (90, 160, 230), (40, 90, 170))
    c.circle(400, 430, 230, (255, 205, 60))
    c.circle(320, 370, 30, BLACK)
    c.circle(480, 370, 30, BLACK)
    c.rect(300, 520, 200, 26, BLACK)
    c.rect(280, 500, 26, 30, BLACK)
    c.rect(494, 500, 26, 30, BLACK)
    _outlined(c, 30, "WHEN THE BUILD PASSES", 5)
    _outlined(c, 110, "ON THE FIRST TRY", 5)
    _outlined(c, 690, "AND NOBODY KNOWS WHY", 5)
    return c.save(path)


def captioned_photo(path: Path) -> Path:
    """A landscape (sky, sun, mountains, a field) with a small caption in a corner."""
    c = Canvas(1200, 900)
    c.vgradient(0, 560, (120, 175, 235), (215, 232, 245))
    c.circle(930, 170, 70, (255, 236, 160))
    c.mountain(300, 230, 600, 380, (95, 110, 130))
    c.mountain(700, 170, 600, 420, (75, 90, 110))
    c.mountain(720, 170, 260, 65, (240, 244, 248))
    c.mountain(1050, 300, 600, 300, (105, 120, 140))
    c.vgradient(560, 340, (90, 150, 70), (50, 105, 45))
    c.speckle(4000, (70, 125, 55), seed=7)
    caption = "Pico dos Marins, Serra da Mantiqueira - julho de 2026"
    width = text_width(caption, 2)
    c.rect(1200 - width - 40, 900 - 50, width + 40, 50, (25, 25, 25))
    c.text(1200 - width - 20, 900 - 38, caption, 2, WHITE)
    return c.save(path)


RECEIPT_LINES = (
    "PADARIA BELA VISTA",
    "CUPOM 004512",
    "12/11/2026   08:41",
    "------------------------",
    "2 PAO FRANCES       1,60",
    "1 CAFE COM LEITE    6,50",
    "1 BOLO DE FUBA      7,90",
    "1 SUCO DE LARANJA   8,00",
    "------------------------",
    "TOTAL R$           24,00",
    "CARTAO DEBITO      24,00",
    "",
    "OBRIGADO, VOLTE SEMPRE!",
)


def receipt_photo(path: Path) -> Path:
    """A phone photo of a till receipt lying on a wooden table."""
    c = Canvas(1000, 1300)
    c.vgradient(0, 1300, (120, 80, 50), (85, 55, 35))
    for i in range(0, 1300, 46):
        c.rect(0, i, 1000, 3, (100, 66, 42))
    scale = 3
    paper_w = 24 * CELL_W * scale + 80
    x0, y0 = (1000 - paper_w) // 2, 170
    paper_h = len(RECEIPT_LINES) * (CELL_H * scale + 8) + 120
    c.rect(x0 + 14, y0 + 14, paper_w, paper_h, (60, 40, 25))
    c.rect(x0, y0, paper_w, paper_h, (248, 246, 240))
    for i, line in enumerate(RECEIPT_LINES):
        c.text(x0 + 40, y0 + 60 + i * (CELL_H * scale + 8), line, scale, INK)
    return c.save(path)


SCANNED_TITLE = "DECLARAÇÃO ANUAL DE QUITAÇÃO DE DÉBITOS"
SCANNED_BODY = (
    "A Companhia de Saneamento Rio Claro declara, para os devidos fins, que "
    "o imóvel abaixo identificado não possui débitos referentes aos serviços "
    "de abastecimento de água e de coleta de esgoto relativos ao ano de 2025, "
    "conforme as faturas pagas e registradas em nosso sistema até a data de "
    "emissão desta declaração. Esta declaração substitui, para comprovação "
    "junto a terceiros, as quitações mensais das faturas do período de "
    "janeiro a dezembro de 2025. Caso existam faturas em discussão judicial "
    "ou administrativa, elas não estão incluídas nesta declaração. A emissão "
    "segue a lei que obriga as prestadoras de serviços públicos a enviar a "
    "declaração anual de quitação aos seus clientes. Matrícula do imóvel e "
    "dados de consumo estão disponíveis na agência virtual. Dúvidas podem ser "
    "esclarecidas na central de atendimento ou em qualquer loja da companhia. "
    "Guarde este documento: ele dispensa a apresentação das contas mensais "
    "do ano a que se refere."
)


def scanned_page(path: Path) -> Path:
    """An A4 page scanned at 150 dpi: a letterhead, a title and dense paragraphs."""
    c = Canvas(1240, 1754, (250, 249, 245))
    c.speckle(2500, (205, 205, 200), seed=11)
    c.rect(110, 120, 80, 80, (40, 90, 160))
    c.text(220, 128, "Companhia de Saneamento Rio Claro", 4, (40, 90, 160))
    c.text(220, 180, "Central de atendimento: agência virtual e lojas", 2, INK)
    c.rect(110, 240, 1020, 3, (40, 90, 160))
    c.centered(300, SCANNED_TITLE, 3, INK)
    y = 400
    for line in _wrap(SCANNED_BODY, 56):
        c.text(110, y, line, 3, INK)
        y += CELL_H * 3 + 12
    y += 40
    for line in ("Ano de referência: 2025", "Data de emissão: 20/05/2026", "Situação: quitado"):
        c.text(110, y, line, 3, INK)
        y += CELL_H * 3 + 12
    c.text(110, 1600, "Documento emitido eletronicamente; dispensa assinatura.", 2, INK)
    return c.save(path)
