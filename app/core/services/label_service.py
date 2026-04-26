"""Сервис для генерации QR-кодов и ярлыков"""

import zipfile
import qrcode
from io import BytesIO
from typing import Optional
from PIL import Image, ImageDraw, ImageFont


class LabelService:
    """Сервис для генерации QR-кодов и ярлыков"""

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

    def generate_zone_qr_zip(self, locations: list, size: int = 300) -> BytesIO:
        """
        Генерирует ZIP-архив с QR-кодами для всех локаций зоны.

        Args:
            locations: список dict с ключами location_code, name_path
            size: размер каждого QR-кода в пикселях

        Returns:
            BytesIO с ZIP-архивом; файлы внутри: {location_code}.png
        """
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for loc in locations:
                png = self.generate_qr_code(
                    loc['location_code'],
                    size=size,
                    name_path=loc.get('name_path'),
                )
                zf.writestr(f"{loc['location_code']}.png", png.read())
        buffer.seek(0)
        return buffer
