"""Сервис для генерации QR-кодов и ярлыков"""

import qrcode
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


class LabelService:
    """Сервис для генерации QR-кодов и ярлыков"""

    def generate_qr_code(self, data: str, size: int = 300) -> BytesIO:
        """
        Генерирует QR-код из строки с текстом под кодом

        Args:
            data: Данные для кодирования (например, location_code)
            size: Размер QR-кода в пикселях (по умолчанию 300x300)

        Returns:
            BytesIO с PNG изображением
        """
        # Создаём QR-код
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )

        qr.add_data(data)
        qr.make(fit=True)

        # Генерируем изображение QR-кода
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img = qr_img.resize((size, size))

        # Создаём новое изображение с местом для текста внизу
        text_height = 60
        total_height = size + text_height

        # Создаём белый фон
        final_img = Image.new('RGB', (size, total_height), 'white')

        # Вставляем QR-код
        final_img.paste(qr_img, (0, 0))

        # Добавляем текст с автоматическим подбором размера шрифта
        draw = ImageDraw.Draw(final_img)

        # Подбираем размер шрифта в зависимости от длины текста
        text_length = len(data)
        if text_length <= 15:
            font_size = 24
        elif text_length <= 25:
            font_size = 20
        elif text_length <= 35:
            font_size = 16
        else:
            font_size = 14

        # Пытаемся загрузить шрифт
        try:
            # Для Debian/Ubuntu (работает в Docker)
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            try:
                # Альтернативный путь
                font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", font_size)
            except:
                # Fallback на дефолтный (без кириллицы)
                font = ImageFont.load_default()

        # Проверяем помещается ли текст в одну строку
        text_bbox = draw.textbbox((0, 0), data, font=font)
        text_width = text_bbox[2] - text_bbox[0]

        # Если текст слишком длинный - уменьшаем шрифт ещё
        max_width = size - 20  # Отступы по бокам
        if text_width > max_width:
            # Пересчитываем размер шрифта
            font_size = int(font_size * (max_width / text_width))
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf", font_size)
                except:
                    font = ImageFont.load_default()

            text_bbox = draw.textbbox((0, 0), data, font=font)
            text_width = text_bbox[2] - text_bbox[0]

        # Рисуем текст по центру
        text_x = (size - text_width) // 2
        text_y = size + 15

        draw.text((text_x, text_y), data, fill='black', font=font)

        # Сохраняем в BytesIO
        buffer = BytesIO()
        final_img.save(buffer, format="PNG")
        buffer.seek(0)

        return buffer