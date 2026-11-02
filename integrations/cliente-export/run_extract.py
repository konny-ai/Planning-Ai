"""Read-only CLIENTE extraction. Uses declared X_CLIENTE_EXPORT views when configured, legacy SELECTs otherwise."""
import csv, glob, json, os, re, pyodbc
SERVER = os.environ.get('CLIENTE_SERVER', r'SERVER\ISTANZA'); DB = os.environ.get('CLIENTE_DB', 'Factory_Cliente')

def odbc_value(value):
    return '{' + str(value).replace('}', '}}') + '}'

parts = ['DRIVER={'+os.environ.get('CLIENTE_DRIVER','ODBC Driver 17 for SQL Server')+'}', f'SERVER={odbc_value(SERVER)}',
         f'DATABASE={odbc_value(DB)}', 'Encrypt=yes', 'TrustServerCertificate=yes', 'ApplicationIntent=ReadOnly']
if os.environ.get('CLIENTE_USER'):
    parts += [f'UID={odbc_value(os.environ["CLIENTE_USER"])}', f'PWD={odbc_value(os.environ.get("CLIENTE_PASSWORD", ""))}']
else:
    parts.append('Trusted_Connection=yes')
cn = pyodbc.connect(';'.join(parts), readonly=True)
here = os.path.dirname(os.path.abspath(__file__))
extract_out = os.path.abspath(os.environ.get('CLIENTE_EXTRACT_OUT') or os.path.join(here, 'out'))
raw_views = os.environ.get('CLIENTE_VIEWS_JSON')
if raw_views:
    views = json.loads(raw_views)
    expected = {
        '10_nesting_programmi': '10_nesting_programmi/1.csv',
        '11_nesting_membri_1': '11_nesting_membri/1.csv',
        '11_nesting_membri_2': '11_nesting_membri/2.csv',
        '12_nesting_lamiere': '12_nesting_lamiere/1.csv',
        '13_materiali_giacenze_arrivi_1': '13_materiali_giacenze_arrivi/1.csv',
        '13_materiali_giacenze_arrivi_2': '13_materiali_giacenze_arrivi/2.csv',
        '13_materiali_giacenze_arrivi_3': '13_materiali_giacenze_arrivi/3.csv',
        '14_sedi_1': '14_sedi/1.csv',
        '14_sedi_2': '14_sedi/2.csv',
        '15_avanzamento_trd': '15_avanzamento_trd/1.csv',
        '16_stati': '16_stati/1.csv',
    }
    if set(views) != set(expected):
        raise ValueError('CLIENTE_VIEWS_JSON: mappatura viste incompleta')
    for label, value in views.items():
        if not isinstance(value, str) or not re.fullmatch(r'(?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*', value):
            raise ValueError(f'{label}: nome tabella/vista non valido')
        parts_name = value.split('.')
        table = '.'.join('[' + part + ']' for part in parts_name)
        cur = cn.cursor(); cur.execute('SELECT * FROM ' + table); rows = cur.fetchall()
        out_path = os.path.join(extract_out, *expected[label].split('/'))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', newline='', encoding='utf-8') as fh:
            writer = csv.writer(fh); writer.writerow([c[0] for c in cur.description]); writer.writerows(rows)
        print(f'{label} {value}: {len(rows)} righe')
        cur.close()
    if os.environ.get('CLIENTE_SYNC_MATERIALS') == '1':
        # Supplement the existing view contract without altering any SQL object.
        def quoted(label):
            return '.'.join('[' + p + ']' for p in views[label].split('.'))
        queries = {
            'material-items.csv': 'SELECT DISTINCT RTRIM(P.PACOD) AS partCode, RTRIM(P.PAUDM) AS uom, P.PADSC AS description FROM dbo.A_PAR P JOIN '
                + quoted('13_materiali_giacenze_arrivi_1') + ' V ON V.sheetPartCode=P.PACOD',
            'purchase-residuals.csv': 'SELECT RTRIM(O.PACOD) AS partCode, A.ORCOD AS purchaseOrderCode, O.NMRIG AS rowNo, '
                'O.PAQTA AS orderedQty, O.PAQCO AS deliveredQty, RTRIM(O.PAUDM) AS uom FROM dbo.L_OFPA O '
                'JOIN dbo.A_ORF A ON A.IDORF=O.IDORF JOIN ' + quoted('13_materiali_giacenze_arrivi_2')
                + ' V ON V.partCode=O.PACOD AND V.purchaseOrderCode=A.ORCOD AND V.rowNo=O.NMRIG',
        }
        for name, query in queries.items():
            cur=cn.cursor();cur.execute(query);rows=cur.fetchall()
            with open(os.path.join(extract_out,name),'w',newline='',encoding='utf-8') as fh:
                writer=csv.writer(fh);writer.writerow([c[0] for c in cur.description]);writer.writerows(rows)
            print(f'{name}: {len(rows)} righe');cur.close()
else:
    for f in sorted(glob.glob(os.path.join(here, '[0-9][0-9]_*.sql'))):
        name = os.path.splitext(os.path.basename(f))[0]; out = os.path.join(extract_out, name); os.makedirs(out, exist_ok=True)
        cur = cn.cursor(); cur.execute('SET NOCOUNT ON;\n' + open(f, encoding='utf-8').read()); i = 0
        while True:
            if cur.description:
                i += 1; rows = cur.fetchall()
                with open(os.path.join(out, f'{i}.csv'), 'w', newline='', encoding='utf-8') as fh:
                    w = csv.writer(fh); w.writerow([c[0] for c in cur.description]); w.writerows(rows)
                print(f'{name} rs{i}: {len(rows)} righe')
            if not cur.nextset(): break
cn.close()
