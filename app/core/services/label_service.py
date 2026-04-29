"""Сервис для генерации QR-кодов и ярлыков"""

import zipfile
import qrcode
from io import BytesIO
from typing import Optional
from PIL import Image, ImageDraw, ImageFont


class LabelService:
    """Сервис для генерации QR-кодов и ярлыков"""

    _pdf_font_name: Optional[str] = None

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _fit_font(self, draw: ImageDraw.Draw, text: str, max_width: int, start_size: int) -> tuple:
        """Уменьшает шрифт до тех пор, пока текст не войдёт в max_width."""
        font = self._load_font(start_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        if text_width > max_width:
            adjusted_size = max(8, int(start_size * max_width / text_width))
            font = self._load_font(adjusted_size)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
        return font, text_width

    def _get_pdf_font_name(self) -> str:
        """Регистрирует шрифт с поддержкой кириллицы для PDF (однократно)."""
        if LabelService._pdf_font_name is not None:
            return LabelService._pdf_font_name

        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf",
        ):
            try:
                pdfmetrics.registerFont(TTFont("WMSLabelFont", path))
                LabelService._pdf_font_name = "WMSLabelFont"
                return "WMSLabelFont"
            except Exception:
                continue

        LabelService._pdf_font_name = "Helvetica-Bold"
        return "Helvetica-Bold"

    def _generate_raw_qr_image(self, data: str, size: int) -> BytesIO:
        """Генерирует PNG только с QR-кодом, без текстовых подписей."""
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img = qr_img.resize((size, size))
        buf = BytesIO()
        qr_img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    def generate_qr_code(
        self,
        data: str,
        size: int = 300,
        name_path: Optional[str] = None,
    ) -> BytesIO:
        """
        Генерирует QR-код из строки с текстом под кодом.

        Args:
            data: Данные для кодирования (location_code)
            size: Размер QR-кода в пикселях
            name_path: Полный путь по названиям (Склад > Зона > … > Ячейка),
                       отображается второй строкой под кодом
        """
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)

        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img = qr_img.resize((size, size))

        # Убираем первый элемент пути (название склада) из отображаемого пути
        display_path: Optional[str] = None
        if name_path:
            parts = name_path.split(' > ')
            display_path = ' > '.join(parts[1:]) if len(parts) > 1 else name_path

        # Высота текстовой области: одна строка = 70px, две строки = 120px
        text_height = 120 if display_path else 70
        total_height = size + text_height

        final_img = Image.new('RGB', (size, total_height), 'white')
        final_img.paste(qr_img, (0, 0))
        draw = ImageDraw.Draw(final_img)

        max_width = size - 20

        # --- Первая строка: location_code без префикса склада ---
        display_code = data.split('-', 1)[1] if '-' in data else data
        code_len = len(display_code)
        if code_len <= 15:
            start_size = 30
        elif code_len <= 25:
            start_size = 26
        elif code_len <= 35:
            start_size = 22
        else:
            start_size = 18

        font_code, code_width = self._fit_font(draw, display_code, max_width, start_size)
        code_x = (size - code_width) // 2
        code_y = size + 12
        draw.text((code_x, code_y), display_code, fill='black', font=font_code)

        # --- Вторая строка: путь по именам без названия склада ---
        if display_path:
            font_path, path_width = self._fit_font(draw, display_path, max_width, 18)
            path_x = (size - path_width) // 2
            path_y = size + 52
            draw.text((path_x, path_y), display_path, fill='#222222', font=font_path)

        buffer = BytesIO()
        final_img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    def _draw_rotated_text(
        self,
        c,
        text: str,
        center_x: float,
        center_y: float,
        font_name: str,
        font_size: int,
        max_len_pt: float,
    ) -> int:
        """Рисует текст повёрнутым на 90° и возвращает итоговый размер шрифта."""
        actual_w = c.stringWidth(text, font_name, font_size)
        if actual_w > 0:
            font_size = max(5, int(font_size * max_len_pt / actual_w))
        c.saveState()
        c.translate(center_x, center_y)
        c.rotate(90)
        c.setFont(font_name, font_size)
        c.drawCentredString(0, 0, text)
        c.restoreState()
        return font_size

    def generate_zone_qr_pdf(self, locations: list, size: int = 300) -> BytesIO:
        """
        Генерирует PDF со всеми QR-кодами зоны, расположенными бок о бок.

        Формат ярлыка: QR-код слева, две строки текста (код + путь) повёрнуты на 90° справа.
        Ярлыки заполняются сверху вниз (по столбцам) на страницах A4 (альбомная).

        Args:
            locations: список dict с ключами location_code, name_path
            size: размер QR-кода в пикселях (используется при генерации растра)

        Returns:
            BytesIO с PDF-файлом
        """
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.utils import ImageReader

        font_name = self._get_pdf_font_name()

        margin = 5.0  # маленький отступ вокруг ярлыка

        # Размеры одного ярлыка
        qr_pt = 150.0
        # Полоса справа от QR: три независимые вертикальные колонки текста.
        # Раньше смещения были слишком плотными, из-за чего третья колонка могла
        # визуально наезжать на соседние строки при длинных кириллических подписях.
        text_strip_padding = 8.0
        text_column_width = 18.0
        text_column_gap = 8.0
        right_extra_padding = 10.0
        text_strip = (
            text_strip_padding * 2
            + text_column_width * 3
            + text_column_gap * 2
            + right_extra_padding
        )
        label_w = qr_pt + text_strip
        label_h = qr_pt

        # Страница = ярлык + отступ со всех сторон
        page_w = label_w + 2 * margin
        page_h = label_h + 2 * margin
        page_size = (page_w, page_h)

        x = margin
        y = margin

        buffer = BytesIO()
        c = rl_canvas.Canvas(buffer, pagesize=page_size)

        for idx, loc in enumerate(locations):
            if idx > 0:
                c.showPage()

            # QR-код (чистый, без текста)
            png = self._generate_raw_qr_image(loc['location_code'], size=200)
            c.drawImage(ImageReader(png), x, y, width=qr_pt, height=qr_pt)

            code = loc['location_code']
            display_code = code.split('-', 1)[1] if '-' in code else code

            column_centers = [
                x + qr_pt + text_strip_padding + text_column_width / 2,
                x + qr_pt + text_strip_padding + text_column_width * 1.5 + text_column_gap,
                x + qr_pt + text_strip_padding + text_column_width * 2.5 + text_column_gap * 2,
            ]

            # Строка 1: код локации без складского префикса
            self._draw_rotated_text(
                c, display_code,
                center_x=column_centers[0],
                center_y=y + label_h / 2,
                font_name=font_name,
                font_size=9,
                max_len_pt=label_h - 4,
            )

            name_path = loc.get('name_path') or ''
            parts = name_path.split(' > ') if name_path else []

            # Строка 2: "Зона: <название зоны>" (parts[1])
            if len(parts) > 1:
                zone_label = f"Зона: {parts[1]}"
                self._draw_rotated_text(
                    c, zone_label,
                    center_x=column_centers[1],
                    center_y=y + label_h / 2,
                    font_name=font_name,
                    font_size=9,
                    max_len_pt=label_h - 4,
                )

            # Строка 3: путь начиная со стеллажа (parts[2:])
            if len(parts) > 2:
                rack_path = ' > '.join(parts[2:])
                rack_font_size = 9
                scaled_rack_font_size = max(
                    5,
                    int(
                        rack_font_size * (label_h - 4)
                        / max(c.stringWidth(rack_path, font_name, rack_font_size), 1)
                    ),
                )
                rack_shift_x = max(0.0, (scaled_rack_font_size - rack_font_size) * 0.55)
                self._draw_rotated_text(
                    c, rack_path,
                    center_x=column_centers[2] + rack_shift_x,
                    center_y=y + label_h / 2,
                    font_name=font_name,
                    font_size=rack_font_size,
                    max_len_pt=label_h - 4,
                )

        c.save()
        buffer.seek(0)
        return buffer

    def generate_zone_qr_zip(self, locations: list, size: int = 300) -> BytesIO:
        """
        Генерирует ZIP-архив с одним PDF-файлом (qr_labels.pdf),
        содержащим QR-коды всех локаций зоны, расположенные бок о бок.

        Args:
            locations: список dict с ключами location_code, name_path
            size: размер каждого QR-кода в пикселях

        Returns:
            BytesIO с ZIP-архивом; внутри один файл: qr_labels.pdf
        """
        pdf_buffer = self.generate_zone_qr_pdf(locations, size)
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("qr_labels.pdf", pdf_buffer.read())
        zip_buffer.seek(0)
        return zip_buffer
