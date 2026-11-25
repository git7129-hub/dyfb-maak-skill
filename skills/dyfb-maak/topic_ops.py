"""
抖店发布面板「话题」操作模块（dyfb-maak v3.0.13，2026-11-04 打通实测）

用途：发布面板作品描述区的**话题添加 / 删除 / 正文复位**。后续所有话题操作一律调用本模块，
不要再走「描述区末尾 execCommand insertText + 点联想项」旧路径（11-03/11-04 实测已失效）。

调用方式（在 computer_use_tool 的 bu cell 内）：
    import sys
    sys.path.insert(0, "/home/user/.doubao/agent_mode/workspace/.user_skills/dyfb-maak")
    import topic_ops

    topic_ops.get_topics()             # 回读当前话题（含背景色，蓝色=rgba(1, 118, 247, 0.12)）
    topic_ops.add_topic_exact("高拍仪")  # 添加一个话题 → True/False
    topic_ops.delete_topic_last()      # 删最后一个话题 → True/False
    topic_ops.delete_topic_mid("高拍仪") # 删指定话题（按词匹配）
    topic_ops.reset_desc_body("正文…")  # 清空描述区并写入合规正文（无话题）

实测要点（勿改）：
1. 进入话题输入模式的唯一入口 = 点描述区下方独立的「#添加话题」文本元素（div class add-E1rqtN），
   JS 按文本节点 indexOf('#添加话题') >= 0 取 parentElement.click()。点击后描述区出现 "#" 前缀、光标在其后。
2. 输入话题词用 CDP：bu.cdp("Input.insertText", text="话题词")，**不带 #**。联想下拉才会出现。
3. 联想项匹配必须「精确前缀 + 词边界」：re.match(r'^#话题词[\\s\\n]', text) 且 text 含「次播放」。
   否则会误选近似词（实测 #高拍仪直播 / #高拍仪模组 各误选一次，需删除重选）。
4. 点击用 bu.click(ref)，2 秒后 topicItem 背景变蓝 rgba(1,118,247,0.12) 才算注册成功。
5. 删除：末尾标签用 setStartAfter + Backspace 一次；中间标签从末尾逐个删，每次回读数量；
   数量未减少 = 光标没落对，重设再删，**禁止连续 Backspace**；topicItem 无删除按钮，禁止 removeChild。
6. 正文复位：先清空描述区（ctrl+a + Backspace，topics=0），光标置末尾 collapse(false)，
   再 CDP insertText 写入正文（普通状态下有效）；**话题标签存在时 setStartBefore + insertText 无效，勿用**。
"""

import re
import time

import seed_browser_use as bu

ED_SELECTOR = '[class*="zone-container"][contenteditable="true"]'
TOPIC_CLASS = '[class*="topicItem"]'
BLUE = "rgba(1, 118, 247, 0.12)"


# ---------------------------------------------------------------- 回读
def get_topics():
    """返回当前话题列表 [{'t': '#词', 'bg': '背景色'}]"""
    js = bu.js(
        """
        (function(){
          var items = document.querySelectorAll('[class*="topicItem"]');
          var out = [];
          for (var i=0;i<items.length;i++){
            out.push({t: items[i].innerText, bg: getComputedStyle(items[i]).backgroundColor});
          }
          return JSON.stringify(out);
        })()
        """
    )
    try:
        import json
        return json.loads(js)
    except Exception:
        return []


def topics_count():
    return len(get_topics())


def all_blue():
    items = get_topics()
    return bool(items) and all(BLUE.replace(" ", "") in (i.get("bg") or "").replace(" ", "") or "1, 118, 247" in (i.get("bg") or "") for i in items)


# ---------------------------------------------------------------- 进入话题输入模式
def _enter_topic_mode():
    """点击「#添加话题」文本元素，进入话题输入模式。返回 True/False"""
    js = bu.js(
        """
        (function(){
          var nodes = document.querySelectorAll('div,span,a');
          for (var i=0;i<nodes.length;i++){
            var n = nodes[i];
            if (n.childNodes && n.childNodes.length === 1 && n.childNodes[0].nodeType === 3
                && (n.childNodes[0].nodeValue || '').indexOf('#添加话题') >= 0) {
              n.parentElement.click();
              return 'CLICKED';
            }
          }
          // 兜底：innerText 精确包含
          for (var j=0;j<nodes.length;j++){
            var m = nodes[j];
            if (m.children.length === 0 && (m.innerText||'').indexOf('#添加话题') >= 0) {
              m.click();
              return 'CLICKED_FALLBACK';
            }
          }
          return 'NOT_FOUND';
        })()
        """
    )
    return js not in ("NOT_FOUND", None)


# ---------------------------------------------------------------- 添加
def add_topic_exact(word, timeout_ticks=14, tick=0.4):
    """添加一个话题（word 不带 #）。成功返回 True，失败返回 False"""
    word = (word or "").strip().lstrip("#")
    if not word:
        return False

    before = topics_count()

    if not _enter_topic_mode():
        return False
    time.sleep(0.8)

    # CDP 真实输入（不带 #）
    bu.cdp("Input.insertText", text=word)

    # 等联想出现（页面出现「次播放」）
    got = False
    for _ in range(timeout_ticks):
        time.sleep(tick)
        if "次播放" in str(bu.get_page_text()):
            got = True
            break
    if not got:
        return False

    # 精确匹配候选
    refs = bu.find(word, limit=15) or []
    target = None
    pat = re.compile(r"^#" + re.escape(word) + r"[\s\n]")
    for ref in refs:
        try:
            t = (bu.text(ref) or "").strip()
        except Exception:
            continue
        if pat.match(t) and "次播放" in t:
            target = ref
            break
    if target is None:
        return False

    bu.click(target)
    time.sleep(2)

    after = topics_count()
    return after > before


# ---------------------------------------------------------------- 删除
def _focus_after_last():
    js = bu.js(
        """
        (function(){
          var ed = document.querySelector('[class*="zone-container"][contenteditable="true"]');
          if (!ed) return 'NO_ED';
          var items = ed.querySelectorAll('[class*="topicItem"]');
          if (!items.length) return 'NO_TOPIC';
          var last = items[items.length-1];
          ed.focus();
          var range = document.createRange();
          range.setStartAfter(last);
          range.collapse(true);
          var sel = window.getSelection();
          sel.removeAllRanges(); sel.addRange(range);
          return 'OK';
        })()
        """
    )
    return js == "OK"


def delete_topic_last(max_try=3):
    """删除最后一个话题（每次回读验证）。成功返回 True"""
    for _ in range(max_try):
        before = topics_count()
        if before == 0:
            return False
        if not _focus_after_last():
            return False
        time.sleep(0.3)
        bu.press_key("Backspace")
        time.sleep(1.0)
        after = topics_count()
        if after == before - 1:
            return True
        # 未减少：重设光标再试（不连按）
    return topics_count() > 0 and topics_count() < before


def delete_topic_mid(substr, max_try=6):
    """删除包含 substr 的话题：从末尾逐个删到目标为止，再把被删的正确话题补回不现实，
    因此仅当目标为末尾时直接删；否则返回 False（建议调用方自行按序处理）。"""
    items = get_topics()
    if not items:
        return False
    words = [i.get("t", "") for i in items]
    idx = None
    for i, w in enumerate(words):
        if substr in w:
            idx = i
            break
    if idx is None:
        return False
    if idx == len(words) - 1:
        return delete_topic_last(max_try=max_try)
    return False


# ---------------------------------------------------------------- 正文复位
def reset_desc_body(body):
    """清空描述区并写入合规正文（会清掉已有话题）。返回写入后的 innerText 片段"""
    # 聚焦 + 全选删除
    js = bu.js(
        """
        (function(){
          var ed = document.querySelector('[class*="zone-container"][contenteditable="true"]');
          if (!ed) return 'NO_ED';
          ed.focus();
          return 'FOCUSED';
        })()
        """
    )
    if js == "NO_ED":
        return None
    bu.hotkey("ctrl", "a")
    time.sleep(0.5)
    bu.press_key("Backspace")
    time.sleep(1.5)

    # 光标置末尾
    bu.js(
        """
        (function(){
          var ed = document.querySelector('[class*="zone-container"][contenteditable="true"]');
          ed.focus();
          var range = document.createRange();
          range.selectNodeContents(ed);
          range.collapse(false);
          var sel = window.getSelection();
          sel.removeAllRanges(); sel.addRange(range);
          return 'CURSOR_END';
        })()
        """
    )
    time.sleep(0.3)
    bu.cdp("Input.insertText", text=body)
    time.sleep(1.5)

    js = bu.js(
        """
        (function(){
          var ed = document.querySelector('[class*="zone-container"][contenteditable="true"]');
          return ed ? ed.innerText.slice(0, 120) : null;
        })()
        """
    )
    return js
