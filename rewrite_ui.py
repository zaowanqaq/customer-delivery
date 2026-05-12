import re
import os

filepath = r"D:\trae international\MediaCrawler\api\webui\ops_config.html"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Replace the <style> block completely
new_style = """  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; background: #F3F4F6; color: #111827; display: flex; height: 100vh; overflow: hidden; }
    .sidebar { width: 260px; background: #ffffff; border-right: 1px solid #E5E7EB; display: flex; flex-direction: column; z-index: 10; flex-shrink: 0; }
    .brand { padding: 24px 20px; border-bottom: 1px solid #E5E7EB; }
    .brand h1 { margin: 0; font-size: 20px; font-weight: 800; color: #B91C1C; letter-spacing: 0.5px; display: flex; align-items: center; gap: 8px; }
    .brand .sub { margin-top: 6px; font-size: 12px; color: #6B7280; font-weight: 500; line-height: 1.4; }
    .nav { flex: 1; overflow-y: auto; padding: 20px 12px; display: flex; flex-direction: column; gap: 4px; }
    .nav-item { padding: 12px 16px; border-radius: 8px; font-size: 14px; font-weight: 500; color: #4B5563; cursor: pointer; display: flex; align-items: center; gap: 10px; transition: all 0.2s; }
    .nav-item:hover { background: #F3F4F6; color: #111827; }
    .nav-item.active { background: #FEF2F2; color: #B91C1C; font-weight: 600; }
    
    .main-content { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: #F9FAFB; }
    .header { height: 64px; background: #ffffff; border-bottom: 1px solid #E5E7EB; display: flex; align-items: center; padding: 0 32px; font-size: 18px; font-weight: 600; color: #111827; flex-shrink: 0; }
    .content-scroll { flex: 1; overflow-y: auto; padding: 32px; }
    
    .wrap { max-width: 960px; margin: 0 auto; display: flex; flex-direction: column; gap: 24px; }
    .tab-pane { display: none; animation: fadeIn 0.3s ease; }
    .tab-pane.active { display: block; }
    
    @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

    .card { background: #fff; border: 1px solid #E5E7EB; border-radius: 12px; padding: 24px; box-shadow: 0 2px 4px rgba(0,0,0,0.02); margin-bottom: 24px; }
    .card h2 { margin: 0 0 8px; font-size: 18px; color: #111827; display: flex; align-items: center; gap: 8px; }
    .hint { margin: 0 0 16px; color: #6b7280; font-size: 13px; line-height: 1.5; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px 20px; }
    .row { display: flex; flex-direction: column; gap: 8px; }
    .full { grid-column: 1 / -1; }
    label { font-size: 13px; color: #374151; font-weight: 600; }
    input, select, textarea { border: 1px solid #D1D5DB; border-radius: 8px; padding: 10px 12px; font-size: 14px; transition: border-color 0.2s, box-shadow 0.2s; outline: none; font-family: inherit; }
    input:focus, select:focus, textarea:focus { border-color: #B91C1C; box-shadow: 0 0 0 3px rgba(185, 28, 28, 0.1); }
    textarea { min-height: 84px; resize: vertical; }
    .btns { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 20px; }
    button { border: 0; border-radius: 8px; padding: 10px 18px; font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; justify-content: center; }
    .primary { background: #B91C1C; color: #fff; box-shadow: 0 1px 2px rgba(185, 28, 28, 0.2); }
    .primary:hover { background: #991B1B; }
    .danger { background: #DC2626; color: #fff; }
    .danger:hover { background: #B91C1C; }
    .ghost { background: #F3F4F6; color: #374151; border: 1px solid #E5E7EB; }
    .ghost:hover { background: #E5E7EB; color: #111827; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    
    .status { margin-top: 0; background: #111827; color: #D1FAE5; border-radius: 12px; padding: 16px; font-size: 13px; white-space: pre-wrap; min-height: 140px; max-height: 300px; overflow-y: auto; font-family: 'JetBrains Mono', Consolas, monospace; box-shadow: inset 0 2px 4px rgba(0,0,0,0.5); }
    .v1-hidden { display: none; }
    .meta-panel { background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 16px; }
    .meta-row { display: flex; justify-content: space-between; gap: 10px; border-bottom: 1px solid #E5E7EB; padding: 10px 0; }
    .meta-row:last-child { border-bottom: none; padding-bottom: 0; }
    .meta-row:first-child { padding-top: 0; }
    .meta-key { color: #4B5563; font-weight: 600; font-size: 13px; }
    .meta-val { color: #111827; font-size: 13px; text-align: right; word-break: break-all; font-weight: 500; }
    .meta-val a { color: #B91C1C; text-decoration: none; }
    .meta-val a:hover { text-decoration: underline; }
    .mono { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12px; background: #F3F4F6; padding: 2px 6px; border-radius: 4px; color: #4B5563; }
    
    .monitor-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .monitor-item { background: #fff; border: 1px solid #E5E7EB; border-radius: 10px; padding: 16px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .monitor-key { font-size: 12px; color: #6B7280; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
    .monitor-val { margin-top: 8px; font-size: 20px; font-weight: 700; color: #111827; }
    .monitor-log { margin-top: 16px; background: #111827; color: #D1FAE5; border-radius: 10px; padding: 16px; min-height: 180px; max-height: 300px; overflow: auto; white-space: pre-wrap; font-size: 12px; font-family: 'JetBrains Mono', Consolas, monospace; }

    /* Custom Scrollbar */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #D1D5DB; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #9CA3AF; }
  </style>"""

content = re.sub(r'<style>.*?</style>', new_style, content, flags=re.DOTALL)

# 2. Extract sections
section_pattern = r'<section class="card">(.*?)</section>'
sections = re.findall(section_pattern, content, flags=re.DOTALL)

# We have 7 sections in the old HTML:
# 0: 项目切换器
# 1: 步骤1：初始化项目
# 2: 项目映射（可视化）
# 3: 公共抓取配置
# 4: 步骤2：样本账号抓取
# 5: 步骤3：爆款抓取
# 6: 抓取进度监控
# 7: 步骤4：同步到多维表
# 8: 步骤5：合作博主监控

status_el_pattern = r'<pre id="status" class="status"></pre>'

# Let's wrap them in our new layout
new_body_start = """<body>
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="brand">
      <h1>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="#B91C1C" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M2 17L12 22L22 17" stroke="#B91C1C" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M2 12L12 17L22 12" stroke="#B91C1C" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        ALIAFANT
      </h1>
      <div class="sub">艾莉芬特 · 全域营销数据中台</div>
    </div>
    <div class="nav">
      <div class="nav-item active" data-tab="tab-project">📁 项目管理</div>
      <div class="nav-item" data-tab="tab-config">⚙️ 公共配置</div>
      <div class="nav-item" data-tab="tab-step2">👤 样本账号抓取</div>
      <div class="nav-item" data-tab="tab-step3">🚀 爆款内容检索</div>
      <div class="nav-item" data-tab="tab-step4">🔄 数据同步中心</div>
      <div class="nav-item" data-tab="tab-step5">🤝 合作笔记监控</div>
      <div class="nav-item" data-tab="tab-monitor">🖥️ 终端运行日志</div>
    </div>
  </div>

  <!-- Main Content -->
  <div class="main-content">
    <div class="header" id="page_header">项目管理</div>
    <div class="content-scroll">
      <div class="wrap">"""

# Tab 1: Project (0, 1, 2)
tab_project = f"""
        <div id="tab-project" class="tab-pane active">
          <section class="card">{sections[0]}</section>
          <section class="card">{sections[1]}</section>
          <section class="card">{sections[2]}</section>
        </div>"""

# Tab 2: Config (3)
tab_config = f"""
        <div id="tab-config" class="tab-pane">
          <section class="card">{sections[3]}</section>
        </div>"""

# Tab 3: Step 2 (4)
tab_step2 = f"""
        <div id="tab-step2" class="tab-pane">
          <section class="card">{sections[4]}</section>
        </div>"""

# Tab 4: Step 3 (5)
tab_step3 = f"""
        <div id="tab-step3" class="tab-pane">
          <section class="card">{sections[5]}</section>
        </div>"""

# Tab 5: Step 4 (7)
tab_step4 = f"""
        <div id="tab-step4" class="tab-pane">
          <section class="card">{sections[7]}</section>
        </div>"""

# Tab 6: Step 5 (8)
tab_step5 = f"""
        <div id="tab-step5" class="tab-pane">
          <section class="card">{sections[8]}</section>
        </div>"""

# Tab 7: Monitor (6 + status)
tab_monitor = f"""
        <div id="tab-monitor" class="tab-pane">
          <section class="card">{sections[6]}</section>
          <section class="card">
            <h2>终端执行日志</h2>
            <pre id="status" class="status"></pre>
          </section>
        </div>"""

new_body_end = """
      </div>
    </div>
  </div>
"""

# Now replace the body wrap logic
# Find everything from <div class="wrap"> up to <pre id="status" class="status"></pre>
wrap_pattern = r'<div class="wrap">.*<pre id="status" class="status"></pre>\s*</div>'

new_body_full = new_body_start + tab_project + tab_config + tab_step2 + tab_step3 + tab_step4 + tab_step5 + tab_monitor + new_body_end

content = re.sub(wrap_pattern, new_body_full, content, flags=re.DOTALL)
content = re.sub(r'<h1>小红书运营配置（V1）</h1>\s*<div class="sub">按流程操作.*?</div>', '', content, flags=re.DOTALL)
content = content.replace("<title>小红书运营配置（V1）</title>", "<title>艾莉芬特 - 小红书全域营销数据中台</title>")

# Inject tab switching JS at the end of the script
js_injection = """
    // --- Tab Switching Logic ---
    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', function() {
        // Update Nav
        document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
        this.classList.add('active');
        
        // Update Header
        const headerText = this.textContent.trim().replace(/^.+?\\s/, ''); // Remove emoji
        document.getElementById('page_header').textContent = headerText;
        
        // Show Pane
        const targetId = this.getAttribute('data-tab');
        document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
        document.getElementById(targetId).classList.add('active');
      });
    });
"""

content = content.replace("</script>", js_injection + "\n  </script>")

with open("api/webui/ops_config_new.html", "w", encoding="utf-8") as f:
    f.write(content)

print("ops_config_new.html written successfully!")
