import json, os, re, subprocess
from cli import load_settings

S = load_settings(prog='build_entries', require=('book',))

# 从 web toc 重建条目（过滤无标题文档）
def get_toc():
    cmd = ['mcporter', 'call', 'yuque-mcp.yuque_web_get_toc',
           '--args', json.dumps({'book_id': S.book_id}), '--output', 'json']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=S.mcp_cwd)
    d = json.loads(r.stdout)
    data = d.get('data', [])
    docs = [x for x in data if x.get('type') == 'DOC' and x.get('title') != '无标题文档']
    return docs

def classify(title):
    """返回 (source, category)"""
    t = title
    # 来源
    source = '其他'
    if 'LINUX DO' in t or 'linux.do' in t:
        source = 'LINUX DO'
    elif 'B站' in t or 'bilibili' in t or '哔哩' in t or re.search(r'BV[\w]{8,}', t):
        source = 'B站'
    elif 'Bangumi' in t or 'bgm.tv' in t:
        source = 'Bangumi'
    elif '小黑盒' in t or 'xiaoheihe' in t:
        source = '小黑盒'
    elif '博客园' in t or 'cnblogs' in t or '/p/' in t:
        source = '博客园'
    # 已知博客园作者后缀
    cnblog_authors = ['卡卡罗特','老马啸西风','Java陈序员','huangSir','南国微雪','威哥爱编程','Macro','程序员',
                      '一亩三分地','木子','码农','猿人','Java技术栈','程序员小灰','纯洁的微笑','江南一点雨']
    for a in cnblog_authors:
        if t.endswith(a) or f'- {a}' in t or f'-{a}' in t:
            source = '博客园'
            break
    
    # 分类
    cat = '其他'
    if re.search(r'日记|随笔|杂谈|周记|月记', t):
        cat = '日记/随笔'
    elif re.search(r'AI|大模型|GPT|ChatGPT|Claude|LLM|OpenAI|DeepSeek|Gemini|Kimi|Sora|Grok|Stable Diffusion|Midjourney', t, re.I):
        cat = 'AI/大模型'
    elif re.search(r'Linux|Ubuntu|CentOS|内核|epoll|僵尸进程|系统|运维|OpenJDK|Docker|K8s|Kubernetes|Nginx|服务器|Shell|环境安装', t, re.I):
        cat = '操作系统'
    elif re.search(r'Java|Spring|秒杀|高并发|Redis|MySQL|RabbitMQ|Maven|Jmeter|MyBatis|编程|代码|开发|架构|微服务|中间件|前端|后端|数据库|算法|面试|Vue|React|Go|Python|C\+\+|JavaScript|TypeScript|Git|Docker|分布式', t, re.I):
        cat = '编程技术'
    elif re.search(r'安全|漏洞|攻防|渗透|黑客|CVE|CTF', t, re.I):
        cat = '安全'
    elif re.search(r'番剧|动漫|动画|新番|Bangumi', t):
        cat = '影视/动漫'
    elif re.search(r'影视|电影|剧集|美剧|日剧|国产剧', t):
        cat = '影视/动漫'
    elif re.search(r'游戏|原神|星穹|崩坏|Steam|Switch|PS5|塞尔达|王国之泪|GTA', t, re.I):
        cat = '游戏'
    elif re.search(r'读书|读后感|书评|小说|修仙|凡人|阅读', t):
        cat = '读书'
    elif re.search(r'音乐|编曲|混音|FL Studio|打谱|吉他|钢琴', t, re.I):
        cat = '音乐制作'
    elif re.search(r'福利|羊毛|薅|优惠|折扣|白嫖', t):
        cat = '福利/羊毛'
    elif re.search(r'投资|股票|基金|理财|财经|A股|美股|比特币|币圈|期货', t, re.I):
        cat = '投资/财经'
    elif re.search(r'工作|求职|简历|面试经验|offer|裁员|跳槽|职场', t, re.I):
        cat = '工作/求职'
    return source, cat

def main():
    docs = get_toc()
    print('DOC 总数:', len(docs))
    entries = []
    for d in docs:
        source, cat = classify(d['title'])
        entries.append({
            'doc_id': str(d['doc_id']),
            'title': d['title'],
            'slug': d.get('url', ''),
            'source': source,
            'category': cat
        })
    os.makedirs(S.state_dir, exist_ok=True)
    with open(S.entries_file, 'w') as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    print('已写入', S.entries_file)
    # 统计
    from collections import Counter
    print('来源分布:', dict(Counter(e['source'] for e in entries)))
    print('分类分布:', dict(Counter(e['category'] for e in entries)))
    print('总条目:', len(entries))

if __name__ == '__main__':
    main()