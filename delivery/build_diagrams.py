from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
FONT_DIR = Path("C:/Windows/Fonts")


def ui_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    filename = "msyhbd.ttc" if bold else "msyh.ttc"
    return ImageFont.truetype(str(FONT_DIR / filename), size)


def canvas(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (1800, 900), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    draw.text((70, 45), title, font=ui_font(38, True), fill="#0B2545")
    draw.text((70, 102), subtitle, font=ui_font(20), fill="#64748B")
    draw.line((70, 145, 1730, 145), fill="#DBEAFE", width=3)
    return image, draw


def rounded_box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    subtitle: str = "",
    fill: str = "#F8FAFC",
    outline: str = "#BFDBFE",
) -> None:
    draw.rounded_rectangle(xy, radius=22, fill=fill, outline=outline, width=3)
    x1, y1, x2, y2 = xy
    lines = [title] + ([subtitle] if subtitle else [])
    line_heights = [42, 32] if subtitle else [42]
    total = sum(line_heights) + (10 if subtitle else 0)
    y = y1 + (y2 - y1 - total) / 2
    for index, text in enumerate(lines):
        f = ui_font(28 if index == 0 else 18, bold=index == 0)
        bbox = draw.multiline_textbbox((0, 0), text, font=f, spacing=7, align="center")
        width = bbox[2] - bbox[0]
        draw.multiline_text(
            ((x1 + x2 - width) / 2, y),
            text,
            font=f,
            fill="#0B2545",
            spacing=7,
            align="center",
        )
        y += line_heights[index] + 10


def arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: str = "#3B82F6",
) -> None:
    draw.line([start, end], fill=color, width=5)
    ex, ey = end
    sx, sy = start
    if abs(ex - sx) >= abs(ey - sy):
        dx = -16 if ex > sx else 16
        points = [(ex, ey), (ex + dx, ey - 10), (ex + dx, ey + 10)]
    else:
        dy = -16 if ey > sy else 16
        points = [(ex, ey), (ex - 10, ey + dy), (ex + 10, ey + dy)]
    draw.polygon(points, fill=color)


def architecture_overview() -> None:
    image, draw = canvas("AgentBI 总体架构", "四层职责分离，LLM 不直接访问数据库")
    boxes = [
        ((50, 320, 285, 510), "业务用户", "自然语言分析"),
        ((345, 270, 645, 560), "Superset / 工作台", "图表 · 筛选 · 身份"),
        ((710, 210, 1110, 620), "AgentBI 编排引擎", "权限 · 上下文 · 路由\n下钻 · 报告 · 证据"),
        ((1180, 270, 1480, 560), "SuperSonic", "语义口径 · 治理查询"),
        ((1540, 320, 1765, 510), "真实数据库", "只读数据源"),
    ]
    for xy, title, subtitle in boxes:
        fill = "#EFF6FF" if title == "AgentBI 编排引擎" else "#F8FAFC"
        rounded_box(draw, xy, title, subtitle, fill=fill)
    for start, end in [
        ((285, 415), (345, 415)),
        ((645, 415), (710, 415)),
        ((1110, 415), (1180, 415)),
        ((1480, 415), (1540, 415)),
    ]:
        arrow(draw, start, end)
    rounded_box(
        draw,
        (760, 680, 1070, 830),
        "LLM 服务",
        "理解与表达",
        fill="#F5F3FF",
        outline="#C4B5FD",
    )
    arrow(draw, (915, 680), (915, 620), color="#8B5CF6")
    draw.text(
        (1150, 742),
        "模型输出必须经过白名单与证据校验",
        font=ui_font(20, True),
        fill="#6D28D9",
    )
    image.save(ASSETS / "architecture-overview.png", quality=95)


def analysis_loop() -> None:
    image, draw = canvas("经营分析闭环", "从当前图表提问，到逐层下钻与报告交付")
    labels = [
        "图表上下文",
        "自然语言问题",
        "意图解析",
        "真实查询",
        "证据校验",
        "动态图表",
        "逐层下钻",
        "AI 分析",
        "分析报告",
        "Word / PDF",
    ]
    for index, label in enumerate(labels):
        row = index // 5
        col = index if row == 0 else 9 - index
        x = 70 + col * 345
        y = 230 + row * 320
        fill = "#EFF6FF" if row == 0 else "#F5F3FF"
        rounded_box(draw, (x, y, x + 260, y + 150), label, f"步骤 {index + 1}", fill=fill)
        if row == 0 and col < 4:
            color = "#3B82F6" if row == 0 else "#8B5CF6"
            arrow(draw, (x + 260, y + 75), (x + 330, y + 75), color=color)
        elif row == 1 and col > 0:
            arrow(draw, (x, y + 75), (x - 70, y + 75), color="#8B5CF6")
    arrow(draw, (1710, 380), (1710, 550), color="#8B5CF6")
    draw.text(
        (590, 790),
        "查询编号 · SQL 指纹 · 筛选路径贯穿全过程",
        font=ui_font(25, True),
        fill="#0B2545",
    )
    image.save(ASSETS / "analysis-loop.png", quality=95)


def model_security_flow() -> None:
    image, draw = canvas("多模型与安全执行", "模型按角色分工，所有输出受治理")
    rounded_box(draw, (80, 250, 390, 410), "用户问题", "当前图表上下文")
    rounded_box(draw, (500, 190, 850, 350), "规则解析", "排名 · 差值 · 占比")
    rounded_box(draw, (500, 430, 850, 590), "问答模型", "复杂意图结构化")
    rounded_box(
        draw,
        (970, 300, 1310, 480),
        "安全校验",
        "字段 · 指标 · 权限 · 操作",
        fill="#FFF7ED",
        outline="#FDBA74",
    )
    rounded_box(draw, (1420, 300, 1730, 480), "真实查询", "SuperSonic")
    arrow(draw, (390, 330), (500, 270))
    arrow(draw, (390, 330), (500, 510), color="#8B5CF6")
    arrow(draw, (850, 270), (970, 350))
    arrow(draw, (850, 510), (970, 430), color="#8B5CF6")
    arrow(draw, (1310, 390), (1420, 390))
    rounded_box(
        draw,
        (330, 680, 680, 830),
        "建模模型",
        "元数据 -> 候选模型",
        fill="#F0FDF4",
        outline="#86EFAC",
    )
    rounded_box(
        draw,
        (760, 680, 1110, 830),
        "人工审核",
        "确认口径后发布",
        fill="#F0FDF4",
        outline="#86EFAC",
    )
    rounded_box(
        draw,
        (1190, 680, 1540, 830),
        "报告模型",
        "证据 -> 发现与建议",
        fill="#F5F3FF",
        outline="#C4B5FD",
    )
    arrow(draw, (680, 755), (760, 755), color="#16A34A")
    arrow(draw, (1110, 755), (1190, 755), color="#8B5CF6")
    draw.text((1360, 520), "数字不可由模型改写", font=ui_font(22, True), fill="#B45309")
    image.save(ASSETS / "model-security-flow.png", quality=95)


def build_diagrams() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    architecture_overview()
    analysis_loop()
    model_security_flow()


if __name__ == "__main__":
    build_diagrams()
