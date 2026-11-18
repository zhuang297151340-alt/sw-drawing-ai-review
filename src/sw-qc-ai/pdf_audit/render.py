# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, asdict

try:
    import pymupdf
except ImportError:  # PyMuPDF 旧包名
    import fitz as pymupdf

from PIL import Image, ImageStat


class PdfInputError(RuntimeError):
    pass


@dataclass
class PageArtifact:
    page: int
    width_pt: float
    height_pt: float
    rotation: int
    text: str
    text_chars: int
    drawings: int
    images: int
    overview: str
    tiles: list[dict]
    blank_ratio: float
    analysis_overview: str = ""

    def public_dict(self) -> dict:
        d = asdict(self)
        d.pop("text", None)
        return d


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _blank_ratio(path: str) -> float:
    with Image.open(path) as im:
        gray = im.convert("L")
        gray.thumbnail((500, 500))
        hist = gray.histogram()
        total = max(1, sum(hist))
        # 接近白色像素占比，只作为空白页门禁，不用于审图判断。
        return round(sum(hist[248:]) / float(total), 5)


def inspect_and_render(pdf_path: str, work_dir: str, expected_pages: int = 0) -> tuple[dict, list[PageArtifact]]:
    if not os.path.isfile(pdf_path):
        raise PdfInputError("PDF 不存在：" + pdf_path)
    os.makedirs(work_dir, exist_ok=True)
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        raise PdfInputError("PDF 无法打开：%s" % exc) from exc
    try:
        if getattr(doc, "needs_pass", False):
            raise PdfInputError("PDF 已加密，无法审核。请先解除密码保护。")
        page_count = len(doc)
        if page_count <= 0:
            raise PdfInputError("PDF 没有页面。")
        if expected_pages and page_count != expected_pages:
            raise PdfInputError("SolidWorks 图纸共 %d 页，但导出的 PDF 为 %d 页，已停止审核。"
                                % (expected_pages, page_count))

        pages: list[PageArtifact] = []
        for index, page in enumerate(doc):
            page_no = index + 1
            page_dir = os.path.join(work_dir, "page_%03d" % page_no)
            os.makedirs(page_dir, exist_ok=True)
            overview = os.path.join(page_dir, "p%03d_overview.png" % page_no)
            page.get_pixmap(dpi=170, alpha=False).save(overview)

            rect = page.rect
            overlap = 0.035
            tiles: list[dict] = []
            for row in range(2):
                for col in range(2):
                    x0 = max(0.0, rect.width * (col * 0.5 - (overlap if col else 0.0)))
                    y0 = max(0.0, rect.height * (row * 0.5 - (overlap if row else 0.0)))
                    x1 = min(rect.width, rect.width * ((col + 1) * 0.5 + (overlap if col == 0 else 0.0)))
                    y1 = min(rect.height, rect.height * ((row + 1) * 0.5 + (overlap if row == 0 else 0.0)))
                    clip = pymupdf.Rect(x0, y0, x1, y1)
                    crop_id = "P%03d-T%d%d" % (page_no, row + 1, col + 1)
                    path = os.path.join(page_dir, crop_id + ".png")
                    page.get_pixmap(dpi=320, alpha=False, clip=clip).save(path)
                    # 首轮用较轻的分区图；关键标注有疑点时再提交上面的 320 DPI 原图。
                    analysis_path = os.path.join(page_dir, crop_id + "_review.png")
                    page.get_pixmap(dpi=240, alpha=False, clip=clip).save(analysis_path)
                    tiles.append({
                        "crop_id": crop_id,
                        "page_bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                        "path": path,
                        "analysis_path": analysis_path,
                    })

            text = page.get_text("text") or ""
            try:
                drawings = len(page.get_drawings())
            except Exception:
                drawings = 0
            try:
                images = len(page.get_images(full=True))
            except Exception:
                images = 0
            analysis_overview = ""
            if len(text) >= 120 and drawings > 0:
                analysis_overview = os.path.join(page_dir, "p%03d_overview_review.png" % page_no)
                page.get_pixmap(dpi=240, alpha=False).save(analysis_overview)
            blank = _blank_ratio(overview)
            if blank > 0.9995 and not text.strip() and drawings == 0 and images == 0:
                raise PdfInputError("PDF 第 %d 页为空白页，已停止审核。" % page_no)
            pages.append(PageArtifact(
                page=page_no,
                width_pt=round(rect.width, 2),
                height_pt=round(rect.height, 2),
                rotation=int(page.rotation or 0),
                text=text,
                text_chars=len(text),
                drawings=drawings,
                images=images,
                overview=overview,
                tiles=tiles,
                blank_ratio=blank,
                analysis_overview=analysis_overview,
            ))
        meta = {
            "file_name": os.path.basename(pdf_path),
            "sha256": sha256_file(pdf_path),
            "page_count": page_count,
            "file_size": os.path.getsize(pdf_path),
            "has_text_layer": any(p.text_chars > 0 for p in pages),
        }
        return meta, pages
    finally:
        doc.close()

