"""Bounded OOXML transport for the versioned, self-contained planning workbook.

The reader selects named Excel tables, never the first nonempty worksheet row.
No formulas, external links, macros, SQL or network requests are executed here.
The product exporter uses the Python standard library, like the existing XLSX
reader: installations do not need Excel, Node or a spreadsheet service to run.
"""
from decimal import Decimal
from importlib.resources import files
from pathlib import Path, PurePosixPath
import json
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from planning_factory.domain.validation import ValidationError

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS = {'m': MAIN}
MAX_FILE = 64 * 1024 * 1024
MAX_PART = 64 * 1024 * 1024
MAX_ROWS = 500_000
PREFIX = 'pf_'


def catalogue():
    return json.loads(files('planning_factory').joinpath(
        'schemas/planning-workbook-v1.json').read_text(encoding='utf-8'))


def columns(code):
    return [f for f in catalogue()['fields'] if f['table'] == code]


def letters(number):
    result = ''
    while number:
        number, rem = divmod(number - 1, 26)
        result = chr(65 + rem) + result
    return result


def coordinates(address):
    match = re.fullmatch(r'\$?([A-Z]{1,3})\$?([1-9][0-9]*)', address)
    if not match:
        raise ValidationError([f'invalid workbook cell reference {address!r}'])
    col = 0
    for char in match[1]:
        col = col * 26 + ord(char) - 64
    if col > 16384 or int(match[2]) > 1048576:
        raise ValidationError(['workbook cell reference exceeds Excel limits'])
    return int(match[2]), col


def _target(parent, target):
    resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else
                                 posixpath.join(posixpath.dirname(parent), target))
    if '\\' in resolved or resolved.startswith('../') or not resolved.startswith('xl/'):
        raise ValidationError(['workbook relationship outside xl/ is not permitted'])
    return resolved


def read_tables(path, *, for_sync=False):
    """Return (table rows, row locators, SHA256) preserving Decimal and explicit null.

    Blank cells are omitted, so zero, false, missing and the literal ``null``
    remain different. A populated formula without a cached value is rejected
    rather than read as zero or skipped along with the row.
    """
    import hashlib
    path = Path(path)
    if path.suffix.lower() != '.xlsx' or path.stat().st_size > MAX_FILE:
        raise ValidationError(['a planning .xlsx under 64 MiB is required'])
    definitions = {t['code']: t for t in catalogue()['tables']}
    result, locations = {}, {}
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or len(names) > 5000:
                raise ValidationError(['duplicate or excessive XLSX package parts'])
            if any('vbaProject' in n or n.startswith('xl/externalLinks/') for n in names):
                raise ValidationError(['macros and external workbook links are not permitted'])
            total = sum(i.file_size for i in archive.infolist())
            if total > 256 * 1024 * 1024:
                raise ValidationError(['expanded workbook exceeds 256 MiB'])

            def xml(name):
                info = archive.getinfo(name)
                if info.file_size > MAX_PART:
                    raise ValidationError([f'{name}: XLSX part exceeds size limit'])
                content = archive.read(name)
                if b'<!DOCTYPE' in content or b'<!ENTITY' in content:
                    raise ValidationError(['XML entity declarations are not permitted'])
                return ET.fromstring(content)

            def rels(parent):
                p = PurePosixPath(parent)
                relpath = str(p.parent / '_rels' / (p.name + '.rels'))
                if relpath not in names:
                    return {}
                rows = list(xml(relpath))
                if any(r.get('TargetMode') == 'External' for r in rows
                       if r.get('Type', '').endswith(('/worksheet', '/table'))):
                    raise ValidationError(['external worksheet/table references are not permitted'])
                return {r.get('Id'): _target(parent, r.get('Target', '')) for r in rows
                        if r.get('Type', '').endswith(('/worksheet', '/table'))}

            shared = []
            if 'xl/sharedStrings.xml' in names:
                shared = [''.join(t.text or '' for t in row.iter(f'{{{MAIN}}}t'))
                          for row in xml('xl/sharedStrings.xml')]
            book_rels = rels('xl/workbook.xml')
            for sheet in xml('xl/workbook.xml').find(f'{{{MAIN}}}sheets'):
                sheet_path = book_rels[sheet.get(f'{{{REL}}}id')]
                root = xml(sheet_path)
                table_rels = rels(sheet_path)
                parts = root.find(f'{{{MAIN}}}tableParts')
                if parts is None:
                    continue
                cells = {};formula_cells=set()
                for c in root.iter(f'{{{MAIN}}}c'):
                    value = c.find(f'{{{MAIN}}}v')
                    kind = c.get('t')
                    if kind == 'inlineStr':
                        decoded = ''.join(t.text or '' for t in c.iter(f'{{{MAIN}}}t'))
                    elif value is None or value.text is None:
                        decoded = None
                    elif kind == 's':
                        decoded = shared[int(value.text)]
                    elif kind == 'b':
                        decoded = value.text == '1'
                    elif kind == 'e':
                        decoded = ValueError(f'Excel error {value.text}')
                    elif kind in ('str', 'd'):
                        decoded = value.text
                    else:
                        decoded = Decimal(value.text)
                    if c.find(f'{{{MAIN}}}f') is not None and decoded is None:
                        decoded = ValueError('formula without cached value; recalculate and save the workbook')
                    if c.find(f'{{{MAIN}}}f') is not None:formula_cells.add(coordinates(c.get('r','')))
                    cells[coordinates(c.get('r', ''))] = decoded
                for part in parts:
                    definition = xml(table_rels[part.get(f'{{{REL}}}id')])
                    name = definition.get('name', '')
                    if not name.startswith(PREFIX):
                        continue  # guides and unrelated presentation tables are not inputs
                    code = name[len(PREFIX):]
                    if code not in definitions or code in result:
                        raise ValidationError([f'unknown or duplicate planning table {name}'])
                    ref = definition.get('ref', '').split(':')
                    if len(ref) != 2:
                        raise ValidationError([f'{name}: rectangular table range required'])
                    (first, left), (last, right) = map(coordinates, ref)
                    if last - first > MAX_ROWS or right - left > 200 or first >= last or left > right:
                        raise ValidationError([f'{name}: invalid or excessive table dimensions'])
                    defs = columns(code)
                    aliases = {f['code']: f['code'] for f in defs}
                    for f in defs:
                        label = f['label']
                        if label in aliases and aliases[label] != f['code']:
                            raise ValidationError([f'{name}: ambiguous schema label {label}'])
                        aliases[label] = f['code']
                    headers = [cells.get((first, c)) for c in range(left, right + 1)]
                    if any(h not in aliases for h in headers):
                        raise ValidationError([f'{name}: unknown or empty column header'])
                    keys = [aliases[h] for h in headers]
                    if len(set(keys)) != len(keys):
                        raise ValidationError([f'{name}: duplicate column header'])
                    result[code], locations[code] = [], []
                    for r in range(first + 1, last + 1):
                        row = {key: cells.get((r, c)) for key, c in zip(keys, range(left, right + 1))}
                        if all(v is None or v == '' for v in row.values()):
                            continue
                        omitted=[]
                        for key, value in row.items():
                            if for_sync and (r,left+keys.index(key)) in formula_cells and not (code=='operation' and (
                                key=='duration_seconds' and row.get('work_time_mode') or key=='setup_seconds' and row.get('setup_frequency'))):
                                raise ValidationError([f'{sheet.get("name")}:{r}:{key}: formula personalizzata; trasformarla in input esplicito prima di sincronizzare, per non perderla'])
                            if value=='Da compilare' and code=='operation' and (
                                key=='duration_seconds' and row.get('work_time_mode') or
                                key=='setup_seconds' and row.get('setup_frequency')):
                                omitted.append(key)
                                continue
                            if isinstance(value, ValueError):
                                if 'formula without cached value' in str(value) and code=='operation' and (
                                    key=='duration_seconds' and row.get('work_time_mode') or
                                    key=='setup_seconds' and row.get('setup_frequency')):
                                    omitted.append(key)
                                    continue
                                raise ValidationError([f'{sheet.get("name")}:{r}:{key}: {value}'])
                        result[code].append({k: v for k, v in row.items() if v is not None and v != '' and k not in omitted})
                        locations[code].append(f'{sheet.get("name")}!{letters(left)}{r}:{letters(right)}{r}')
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, ValueError) as exc:
        raise ValidationError([f'cannot read planning workbook: {exc}']) from None
    if 'scenario' not in result or len(result['scenario']) != 1:
        raise ValidationError(['planning workbook requires exactly one row in named table pf_scenario'])
    return result, locations, hashlib.sha256(path.read_bytes()).hexdigest()


def write_tables(path, tables):
    """Portable product export: one readable, filterable named table per entity.

    Values and metadata are inputs. Formula evaluation belongs to the importer;
    no formula cache or external workbook is required to reproduce a plan.
    """
    path = Path(path)
    if path.suffix.lower() != '.xlsx':
        raise ValidationError(['workbook output must have .xlsx extension'])
    cat = catalogue()
    known = {t['code'] for t in cat['tables']}
    if set(tables) - known:
        raise ValidationError([f'unknown export tables {sorted(set(tables) - known)}'])
    path.parent.mkdir(parents=True, exist_ok=True)

    def cell(address, value, style=0):
        attrs = f'r={quoteattr(address)} s="{style}"'
        if value is None:
            return f'<c {attrs} t="inlineStr"><is><t>null</t></is></c>'
        if isinstance(value, bool):
            return f'<c {attrs} t="b"><v>{int(value)}</v></c>'
        if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
            return f'<c {attrs}><v>{value}</v></c>'
        text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        return f'<c {attrs} t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'

    workbook, relations, content = [], [], []
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        guide_name='00 Indice'
        workbook.append(f'<sheet name="{guide_name}" sheetId="1" r:id="guide"/>')
        relations.append(f'<Relationship Id="guide" Type="{REL}/worksheet" Target="worksheets/guide.xml"/>')
        content.append('<Override PartName="/xl/worksheets/guide.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        guide_rows=[f'<row r="2" ht="28" customHeight="1">{cell("A2","Dati completi per la pianificazione",1)}</row>']
        guide_rows.append(f'<row r="3" ht="24" customHeight="1">{cell("A3","Fai clic sul nome del foglio per aprirlo. In ogni foglio, Torna all’indice riporta qui con un clic.",4)}</row>')
        instructions=[
            'Un file contiene dati, profilo, politiche e scelte di calcolo. Versione: planning-workbook-v1.',
            'Modificare i dati nelle tabelle. Non rinominare le tabelle pf_ né aggiungere colonne non previste.',
            'Tempi in minuti, quantità con unità esplicite, date e ore con fuso. Zero e dato mancante sono distinti.',
            'In Fasi: numero macchine e operatori e percentuali indipendenti per attrezzaggio e lavorazione.',
            'Digitare le quote come 25% o 100%. Per assenza operatore nel ciclo: numero 0, quota 0%, presidio assente.',
            'I requisiti macchina/operatore normalizzati derivano dagli otto valori di Fasi. Le candidate restano distinte.',
            'Il motore controlla i dati e segnala i vincoli non supportati. Una colonna compilata non attiva da sola una funzione.',
        ]
        for n,text in enumerate(instructions,4):
            guide_rows.append(f'<row r="{n}" ht="30" customHeight="1">{cell(f"A{n}",text,4)}</row>')
        guide_rows.append('<row r="12" ht="28" customHeight="1">'+''.join(cell(f'{letters(c)}12',v,2) for c,v in enumerate(('Foglio','Area','Una riga rappresenta','Righe dati'),1))+'</row>')
        links=[]
        for n,table in enumerate(cat['tables'],13):
            guide_rows.append(f'<row r="{n}" ht="32" customHeight="1">'+''.join(cell(f'{letters(c)}{n}',v,8 if c==1 else 4) for c,v in enumerate((table['sheet'],table['area'],table['grain'],len(tables.get(table['code'],[]))),1))+'</row>')
            links.append(f'<hyperlink ref="A{n}" location={quoteattr(chr(39)+table["sheet"]+chr(39)+"!A1")} display={quoteattr(table["sheet"])}/>')
        z.writestr('xl/worksheets/guide.xml',f'<worksheet xmlns="{MAIN}"><sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews><cols><col min="1" max="1" width="42" customWidth="1"/><col min="2" max="2" width="25" customWidth="1"/><col min="3" max="3" width="58" customWidth="1"/><col min="4" max="4" width="15" customWidth="1"/></cols><sheetData>{"".join(guide_rows)}</sheetData><mergeCells count="9"><mergeCell ref="A2:D2"/><mergeCell ref="A3:D3"/>'+''.join(f'<mergeCell ref="A{n}:D{n}"/>' for n in range(4,11))+f'</mergeCells><hyperlinks>{"".join(links)}</hyperlinks></worksheet>')
        for i, table in enumerate(cat['tables'], 1):
            code = table['code']
            defs = columns(code)
            source = tables.get(code, [])
            if len(source) > MAX_ROWS:
                raise ValidationError([f'{code}: too many rows to export'])
            all_keys = {f['code'] for f in defs}
            if any(set(row) - all_keys for row in source):
                raise ValidationError([f'{code}: unknown output fields'])
            sheet_name = table['sheet']
            workbook.append(f'<sheet name={quoteattr(sheet_name)} sheetId="{i+1}" r:id="rId{i}"/>')
            relations.append(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>')
            content += [f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>',
                        f'<Override PartName="/xl/tables/table{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>']
            row_xml = [f'<row r="2" ht="26" customHeight="1">{cell("A2", table["name"], 1)}</row>',
                       f'<row r="4" ht="{96 if len(defs)==1 else 48}" customHeight="1">{cell("A4", table["grain"] + ". " + table["description"], 4)}</row>',
                       f'<row r="5" ht="24" customHeight="1">{cell("A5", "Torna all’indice", 8)}</row>',
                       '<row r="7" ht="48" customHeight="1">' + ''.join(cell(f'{letters(c)}7', f['label'], 2) for c, f in enumerate(defs, 1)) + '</row>']
            for r, row in enumerate(source, 8):
                body = []
                refs={f['code']:f'{letters(n)}{r}' for n,f in enumerate(defs,1)}
                for c, f in enumerate(defs, 1):
                    derived=code=='operation' and (f['code']=='duration_seconds' and row.get('work_time_mode') or
                                                   f['code']=='setup_seconds' and row.get('setup_frequency'))
                    if f['code'] not in row and not derived:
                        continue
                    value = row.get(f['code'])
                    style = 5 if f['type'].startswith('Percentuale') else 7 if f['type'].startswith('Decimale') else 3
                    if isinstance(value,str) and f['type'].startswith(('Decimale','Intero','Percentuale')) and re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',value):
                        number=Decimal(value)
                        time_noise=f['code'].endswith(('_seconds','_minutes')) and abs(Decimal(format(number,'.15g'))-number)*60<=Decimal('0.000000001')
                        if len(number.as_tuple().digits)<=15 or time_noise:
                            value=number
                    formula=None
                    if code=='operation' and f['code']=='duration_seconds' and row.get('work_time_mode'):
                        m,q,d,n=(refs[k] for k in ('work_time_mode','quantity','declared_work_minutes','reference_quantity'))
                        formula=f'IF({m}="per_pezzo",{q}*{d},IF({m}="ogni_N_pezzi",{q}*{d}/{n},{d}))'
                    if code=='operation' and f['code']=='setup_seconds' and row.get('setup_frequency'):
                        formula=f'IF(COUNTIFS(pf_setup_phase[Fase],{refs["id"]})=1,SUMIFS(pf_setup_phase[Preparazione nominale (min)],pf_setup_phase[Fase],{refs["id"]})*{refs["setup_occurrences"]},"Da compilare")'
                    if formula is not None:
                        cache=f'<v>{value}</v>' if isinstance(value,(int,Decimal)) and not isinstance(value,bool) else ''
                        body.append(f'<c r="{letters(c)}{r}" s="6"><f>{escape(formula)}</f>{cache}</c>')
                    else:
                        body.append(cell(f'{letters(c)}{r}', value, style))
                row_xml.append(f'<row r="{r}" ht="32" customHeight="1">{"".join(body)}</row>')
            last = max(8, len(source) + 7)
            dims = f'A7:{letters(len(defs))}{last}'
            def width(field):
                if field['code']=='content_hash':return 76
                values = (row.get(field['code']) for row in source)
                longest = max((len(v) + 3 for v in values if isinstance(v, str) and len(v) < 60), default=0)
                return min(56, max(42 if len(defs) == 1 else 20, len(field['label']) + 3, longest))
            widths = ''.join(f'<col min="{n}" max="{n}" width="{width(f)}" customWidth="1"/>'
                             for n, f in enumerate(defs, 1))
            validations=[]
            for n,f in enumerate(defs,1):
                if f['type'].startswith('Percentuale'):
                    validations.append(f'<dataValidation type="decimal" operator="between" allowBlank="1" showErrorMessage="1" errorTitle="Percentuale non valida" error="Inserire un valore fra 0% e 100%" sqref="{letters(n)}8:{letters(n)}{max(last,1000)}"><formula1>0</formula1><formula2>1</formula2></dataValidation>')
            dvs=f'<dataValidations count="{len(validations)}">{"".join(validations)}</dataValidations>' if validations else ''
            sheet = (f'<worksheet xmlns="{MAIN}" xmlns:r="{REL}"><sheetViews><sheetView workbookViewId="0" showGridLines="0">'
                     '<pane xSplit="2" ySplit="7" topLeftCell="C8" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>'
                     f'<cols>{widths}</cols><sheetData>{"".join(row_xml)}</sheetData><tableParts count="1"><tablePart r:id="rTable"/></tableParts></worksheet>')
            navigation=f'<hyperlinks><hyperlink ref="A5" location={quoteattr(chr(39)+guide_name+chr(39)+"!A1")} display="Torna all’indice" tooltip="Apri il foglio 00 Indice"/></hyperlinks>'
            sheet=sheet.replace('<tableParts',dvs+navigation+'<tableParts')
            if len(defs)>1:
                last_title=letters(min(len(defs),6))
                sheet=sheet.replace('</sheetData>',f'</sheetData><mergeCells count="2"><mergeCell ref="A2:{last_title}2"/><mergeCell ref="A4:{last_title}4"/></mergeCells>')
            z.writestr(f'xl/worksheets/sheet{i}.xml', sheet)
            z.writestr(f'xl/worksheets/_rels/sheet{i}.xml.rels', f'<Relationships xmlns="{PKG}"><Relationship Id="rTable" Type="{REL}/table" Target="../tables/table{i}.xml"/></Relationships>')
            table_cols = ''.join(f'<tableColumn id="{n}" name={quoteattr(f["label"])}/>' for n, f in enumerate(defs, 1))
            z.writestr(f'xl/tables/table{i}.xml', f'<table xmlns="{MAIN}" id="{i}" name="{PREFIX}{code}" displayName="{PREFIX}{code}" ref="{dims}" totalsRowShown="0"><autoFilter ref="{dims}"/><tableColumns count="{len(defs)}">{table_cols}</tableColumns><tableStyleInfo name="TableStyleLight1" showRowStripes="1"/></table>')
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>{"".join(workbook)}</sheets><calcPr calcMode="auto"/></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{PKG}">{"".join(relations)}<Relationship Id="styles" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
        z.writestr('_rels/.rels', f'<Relationships xmlns="{PKG}"><Relationship Id="book" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(content) + '</Types>')
        fonts = '<fonts count="3"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="16"/><name val="Arial"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font></fonts>'
        fonts=fonts.replace('count="3"','count="4"').replace('</fonts>','<font><u/><sz val="11"/><color rgb="FF2765C4"/><name val="Arial"/></font></fonts>')
        fills = '<fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF233B53"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF1D6"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEAF0F6"/><bgColor indexed="64"/></patternFill></fill></fills>'
        xfs = '<cellXfs count="8"><xf fontId="0" fillId="0" borderId="0"/><xf fontId="1" fillId="0" borderId="0"/><xf fontId="2" fillId="2" borderId="0" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf><xf fontId="0" fillId="3" borderId="0" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf><xf fontId="0" fillId="0" borderId="0"/><xf fontId="0" fillId="3" borderId="0" numFmtId="10" applyNumberFormat="1"/><xf fontId="0" fillId="4" borderId="0"/><xf fontId="0" fillId="3" borderId="0" numFmtId="2" applyNumberFormat="1" applyAlignment="1"><alignment vertical="center"/></xf></cellXfs>'
        xfs=xfs.replace('<xf fontId="0" fillId="0" borderId="0"/>', '<xf fontId="0" fillId="0" borderId="0" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf>')
        xfs=xfs.replace('count="8"','count="9"').replace('</cellXfs>','<xf fontId="3" fillId="0" borderId="0" applyAlignment="1"><alignment vertical="center"/></xf></cellXfs>')
        z.writestr('xl/styles.xml', f'<styleSheet xmlns="{MAIN}">{fonts}{fills}<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>{xfs}<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    return path
