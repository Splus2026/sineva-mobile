"""Разбивка текста книги на топики (параграфы) по разделителю: Nxxx, NSxxx, Numeric, ExcelRow1, строка.
Взято из Синевы+ (utils/text_processing.py) без изменений логики."""
import re


def split_into_sections_by_separator(lines, separator):
    if separator == "Numeric":
        return _split_numeric(lines)
    elif separator == "ExcelRow1":
        return _split_excel_rows(lines)
    elif separator.startswith('N') and separator != "Numeric":
        if separator.startswith('NS'):
            return _split_semantic(lines, separator)
        else:
            return _split_fixed_size(lines, separator)
    else:
        return _split_by_string(lines, separator)

def _split_excel_rows(lines):
    """Разделитель 'ExcelRow1' для Excel-файлов: каждая строка — один топик.
       К моменту вызова строки уже подготовлены utils/excel_processing.py
       (одна строка листа = одна строка текста, без внутренних переводов
       строк), так что здесь достаточно просто взять их по одной."""
    sections = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        sections.append({
            'lines': [line],
            'title': line.strip()[:100],
            'start_line': i
        })
    return sections

def _split_numeric(lines):
    sections = []
    current_section = []
    current_title = ""
    current_start = 0
    for i, line in enumerate(lines):
        if line.strip() and re.match(r'^\d+', line.strip()):
            if current_section:
                sections.append({
                    'lines': current_section,
                    'title': current_title,
                    'start_line': current_start
                })
            current_title = line.strip()[:100]
            current_section = [line]
            current_start = i
        else:
            current_section.append(line)
    if current_section:
        sections.append({
            'lines': current_section,
            'title': current_title,
            'start_line': current_start
        })
    return sections

def _split_fixed_size(lines, separator):
    try:
        block_size = int(separator[1:])
    except:
        block_size = 100
    if block_size <= 0:
        block_size = 100
    full_text = '\n'.join(lines)
    text_len = len(full_text)
    sections = []
    pos = 0
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line) + 1)
    def get_line_number(pos):
        lo, hi = 0, len(line_offsets) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if line_offsets[mid] <= pos:
                lo = mid + 1
            else:
                hi = mid
        return lo
    while pos < text_len:
        end = min(pos + block_size, text_len)
        if end < text_len:
            space_pos = full_text.rfind(' ', pos, end)
            if space_pos > pos:
                end = space_pos + 1
            else:
                next_space = full_text.find(' ', end)
                # Пробела дальше нет — это ПОСЛЕДНЕЕ слово текста: берём его целиком (раньше его
                # резало ровно на размере блока, если оно длиннее блока).
                end = next_space + 1 if next_space != -1 else text_len
        block_text = full_text[pos:end].strip()
        if block_text:
            line_num = get_line_number(pos)
            sections.append({
                'lines': [block_text],
                'title': f"Блок {len(sections)+1} (символы {pos+1}-{end})",
                'start_line': line_num - 1
            })
        pos = end
    return sections

def _split_semantic(lines, separator):
    target_size = 200
    try:
        target_size = int(separator[2:])
    except:
        pass
    full_text = '\n'.join(lines)
    blocks, positions = split_into_blocks_by_semantic_with_positions(full_text, target_size)
    sections = []
    for i, (block, start_pos, end_pos) in enumerate(zip(blocks, positions['starts'], positions['ends']), 1):
        line_num = 0
        char_count = 0
        for j, line in enumerate(lines):
            if char_count + len(line) + 1 > start_pos:
                line_num = j
                break
            char_count += len(line) + 1
        sections.append({
            'lines': [block],
            'title': f"Блок {i} (смысловой)",
            'start_line': line_num
        })
    return sections

def _split_by_string(lines, separator):
    sections = []
    current_section = []
    current_title = ""
    current_start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith(separator):
            if current_section:
                sections.append({
                    'lines': current_section,
                    'title': current_title,
                    'start_line': current_start
                })
            current_title = line.strip()[:100]
            current_section = [line]
            current_start = i
        else:
            current_section.append(line)
    if current_section:
        sections.append({
            'lines': current_section,
            'title': current_title,
            'start_line': current_start
        })
    return sections


def split_into_blocks_by_semantic_with_positions(text, target_size=200):
    paragraphs = text.split('\n\n')
    blocks = []
    starts = []
    ends = []
    current_block = []
    current_size = 0
    current_pos = 0
    for para in paragraphs:
        if not para.strip():
            continue
        para_start = text.find(para, current_pos)
        if para_start == -1:
            continue
        current_pos = para_start
        sentences = re.split(r'[.!?]\s+', para)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            sent_len = len(sent)
            sent_start = text.find(sent, current_pos)
            if sent_start == -1:
                continue
            if sent_len > target_size * 1.5:
                # Позиции: на каждый блок РОВНО по одному началу и одному концу (начало блока
                # записывается при его открытии, поэтому при закрытии его повторять нельзя — раньше
                # здесь дописывалось лишнее начало, и у всех блоков, кроме первого, позиция
                # оказывалась чужой).
                if current_block:
                    blocks.append(' '.join(current_block))
                    ends.append(current_pos)
                    current_block = []
                    current_size = 0
                words = sent.split()
                temp_block = []
                temp_size = 0
                consumed = 0
                block_start = sent_start
                for word in words:
                    if temp_size + len(word) + 1 > target_size and temp_block:
                        blocks.append(' '.join(temp_block))
                        starts.append(block_start)
                        ends.append(block_start + temp_size - 1)
                        consumed += temp_size
                        block_start = sent_start + consumed
                        temp_block = []
                        temp_size = 0
                    temp_block.append(word)
                    temp_size += len(word) + 1
                if temp_block:
                    blocks.append(' '.join(temp_block))
                    starts.append(block_start)
                    ends.append(sent_start + sent_len)
                current_pos = sent_start + sent_len
                continue
            if current_size + sent_len + 1 > target_size and current_block:
                blocks.append(' '.join(current_block))
                ends.append(current_pos)
                current_block = []
                current_size = 0
            if not current_block:
                starts.append(sent_start)
            current_block.append(sent)
            current_size += sent_len + 1
            current_pos = sent_start + sent_len
    if current_block:
        blocks.append(' '.join(current_block))
        ends.append(current_pos)
    if not blocks and text.strip():
        blocks = [text.strip()]
        starts = [0]
        ends = [len(text)]
    return blocks, {'starts': starts, 'ends': ends}
