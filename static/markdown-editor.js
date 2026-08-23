// h1見出し("# ")・箇条書き("- ")の自動補完/継続、Tabでのインデント、
// Alt+↑/↓での行入れ替えを行う Markdown テキストエリア用の keydown ハンドラ。
(() => {
  const applyMarkdownEditorKeydown = (el, e) => {
    if (e.isComposing || 229 === e.keyCode) return;
    const t = el.selectionStart,
      a = el.value.substring(0, t),
      l = a.split("\n"),
      n = l.length - 1,
      o = l[n],
      r = o.match(/^(\s*)- /),
      c = o.match(/^#\s/);
    if ("Tab" === e.key) {
      e.preventDefault();
      const t = el.selectionStart,
        a = el.selectionEnd,
        l = el.value.slice(0, t),
        n = (el.value.slice(t, a), el.value.slice(a)),
        o = l.lastIndexOf("\n") + 1,
        r = a + n.indexOf("\n"),
        c = el.value.slice(o, r).split("\n");
      if (e.shiftKey) {
        const e = c
          .map((e) => (e.startsWith("  ") ? e.slice(2) : e))
          .join("\n");
        el.value = el.value.slice(0, o) + e + el.value.slice(r);
        const a = t - 2 * c.filter((e) => e.startsWith("  ")).length;
        (el.selectionStart = a), (el.selectionEnd = a);
      } else {
        const e = c.map((e) => "  " + e).join("\n");
        el.value = el.value.slice(0, o) + e + el.value.slice(r);
        const a = t + 2 * c.length;
        (el.selectionStart = a), (el.selectionEnd = a);
      }
    }
    if (e.altKey && ("ArrowUp" === e.key || "ArrowDown" === e.key)) {
      e.preventDefault();
      const t = el.selectionStart,
        a = el.selectionEnd,
        l = el.value.slice(0, t),
        n = (el.value.slice(t, a), el.value.slice(a)),
        o = l.lastIndexOf("\n") + 1,
        r = a + n.indexOf("\n"),
        c = el.value.slice(o, r),
        i = el.value;
      if ("ArrowUp" === e.key) {
        const e = i.lastIndexOf("\n", o - 2) + 1;
        if (-1 !== e) {
          const t = o - 1,
            a = i.slice(e, t);
          (el.value = i.slice(0, e) + c + "\n" + a + i.slice(r + 1)),
            (el.selectionStart = e),
            (el.selectionEnd = e + c.length - 1);
        }
      } else if ("ArrowDown" === e.key) {
        const e = r + 1,
          t = i.indexOf("\n", e);
        if (-1 !== t) {
          const a = i.slice(e, t);
          (el.value = i.slice(0, o) + a + "\n" + c + i.slice(t + 1)),
            (el.selectionStart = o + a.length + 1),
            (el.selectionEnd = el.selectionStart + c.length - 1);
        }
      }
    }
    if ("Enter" === e.key) {
      if (r) {
        e.preventDefault();
        const n = r[1],
          c = el.value.substring(t);
        if ("-" === o.trim()) {
          l.pop();
          const e = l.join("\n") + "\n" + c;
          (el.value = e),
            (el.selectionStart = el.selectionEnd = t - o.length + 1);
        } else {
          const e = a + "\n" + n + "- " + c;
          (el.value = e),
            (el.selectionStart = el.selectionEnd = t + n.length + 3);
        }
      } else if (c && o.trim().length > 2) {
        e.preventDefault();
        const l = a + "\n- " + el.value.substring(t);
        (el.value = l), (el.selectionStart = el.selectionEnd = t + 3);
      } else if (c) {
        // 見出しの "# " だけでタイトル未入力のまま Enter された場合、
        // 新しい見出しを重ねて挿入せず、空の見出し記号を取り消す
        // (空の箇条書きで Enter したときに項目を抜けるのと同じ挙動)。
        e.preventDefault();
        l.pop();
        const suffix = el.value.substring(t),
          newValue = l.join("\n") + "\n" + suffix;
        (el.value = newValue),
          (el.selectionStart = el.selectionEnd = t - o.length + 1);
      } else if (n > 1) {
        e.preventDefault();
        const l = a + "\n\n# " + el.value.substring(t);
        (el.value = l), (el.selectionStart = el.selectionEnd = t + 4);
      }
    } else if ("Backspace" === e.key) {
      if ("-" === o.trim()) {
        e.preventDefault(), l.pop();
        const a = l.join("\n") + el.value.substring(t);
        (el.value = a),
          (el.selectionStart = el.selectionEnd = t - o.length);
      }
    } else if ("#" === e.key && "" === o) {
      e.preventDefault();
      const l = a + "# " + el.value.substring(t);
      (el.value = l), (el.selectionStart = el.selectionEnd = t + 2);
    }
    // 上のいずれかで el.value / selectionStart をプログラムから書き換えた
    // 場合(preventDefaultされている場合)、実際のキー入力と違って
    // "input" イベントが自然発火しないため、保存・高さ再計算・
    // オートスクロールなどが一切走らない。明示的に発火させて
    // el の input ハンドラに処理させる。
    e.defaultPrevented &&
      el.dispatchEvent(new Event("input", { bubbles: !0 }));
  };

  window.applyMarkdownEditorKeydown = applyMarkdownEditorKeydown;
})();
