import csv
from io import StringIO,BytesIO
from openpyxl import Workbook

LABELS={'new':'待核实','contacted':'已联系','replied':'有回复','qualified':'已确认匹配','archived':'已归档'}
HEADERS=['企业/品牌','官网','来源渠道','公开邮箱','公开电话','WhatsApp','社交主页','匹配词','规则匹配分','匹配依据','页面国家线索','搜索目标国家','跟进状态','备注','来源链接与时间','更新时间','名称依据','自动筛选结论','筛选说明','目标客户类型']


def safe_cell(value):
    value=str(value or '').replace('\x00','')
    value=''.join(c for c in value if c in '\t\n\r' or ord(c)>=32)
    if value.lstrip().startswith(('=','+','-','@')): value="'"+value
    return value[:32000]


def export_leads(leads,format):
    if format not in {'csv','xlsx'}: raise ValueError('导出格式只支持 csv/xlsx')
    rows=[HEADERS]
    for r in leads:
        values=[r.get('name'),r.get('website'),r.get('channel'),*[', '.join(r.get(f,[])) for f in ['emails','phones','whatsapp','social_links','keywords']],r.get('score'),'; '.join(r.get('reasons',[])),r.get('country_evidence'),r.get('target_country'),LABELS.get(r.get('status'),r.get('status')),r.get('notes'),'; '.join(s['url']+' '+s['captured_at'] for s in r.get('sources',[])),r.get('updated_at')]
        values.append(r.get('name_source',''))
        values.extend([{'matched':'规则匹配','review':'待核实'}.get(r.get('screening'),''),r.get('screening_reason',''),r.get('buyer_type','')])
        rows.append([safe_cell(v) for v in values])
    if format=='csv':
        out=StringIO(newline=''); csv.writer(out).writerows(rows); return out.getvalue().encode('utf-8-sig')
    book=Workbook(); sheet=book.active; sheet.title='客户线索'
    for row in rows: sheet.append(row)
    sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
    for col in sheet.columns: sheet.column_dimensions[col[0].column_letter].width=26
    out=BytesIO(); book.save(out); return out.getvalue()
