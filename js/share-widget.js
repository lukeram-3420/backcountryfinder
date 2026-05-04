// ── SHARE WIDGET ──
// Standalone, page-agnostic share popover. Used by:
//   - js/saved.js (saved-list toolbar share — URL = ?shared={ids})
//   - templates/progression.html.j2 (progression page share — URL = location.href)
//
// Public API (single entry point):
//
//   openSharePopover({
//     popoverEl,           // HTMLElement — the .share-popover container
//     anchorEl,            // HTMLElement — the button the popover anchors to
//     shareUrl,            // string — URL to share
//     title,               // string — title for native share / "Copied!" feedback
//     headerText,          // optional, default "share this"
//   })
//
// The module owns one document-level click listener that delegates copy /
// native-share button clicks via data-attributes and closes any open popover
// when the user clicks outside. Idempotent — initShareWidget()'s internal
// listener is attached only once even if the script is loaded twice.

(function () {
  if (window.__bcfShareWidgetInit) return;
  window.__bcfShareWidgetInit = true;

  function buildSharePopoverHTML(shareUrl, title, headerText) {
    var msg = encodeURIComponent(shareUrl);
    var canNative = typeof navigator.share === 'function';
    var safeTitle = String(title || 'BackcountryFinder').replace(/"/g, '&quot;');
    var safeUrl = String(shareUrl).replace(/"/g, '&quot;');
    return '<div class="share-popover-title">' + (headerText || 'share this') + '</div>' +
      '<div class="share-popover-btns">' +
        '<a class="sp-btn sp-btn-wa" href="https://wa.me/?text=' + msg + '" target="_blank" rel="noopener">' +
          '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/><path d="M12 0C5.373 0 0 5.373 0 12c0 2.136.564 4.14 1.548 5.871L0 24l6.335-1.521A11.934 11.934 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm0 21.818a9.818 9.818 0 01-5.006-1.369l-.36-.214-3.732.895.944-3.617-.235-.374A9.818 9.818 0 012.182 12C2.182 6.57 6.57 2.182 12 2.182S21.818 6.57 21.818 12 17.43 21.818 12 21.818z"/></svg>WhatsApp</a>' +
        '<a class="sp-btn sp-btn-im" href="sms:&body=' + msg + '">' +
          '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.373 0 0 4.925 0 11c0 3.39 1.643 6.425 4.219 8.399L3 24l4.797-2.561A13.03 13.03 0 0012 22c6.627 0 12-4.925 12-11S18.627 0 12 0z"/></svg>iMessage</a>' +
        (canNative ? '<button class="sp-btn sp-btn-native" data-native-share="' + safeUrl + '" data-native-title="' + safeTitle + '">share via...</button>' : '') +
        '<button class="sp-btn sp-btn-copy" data-copy-url="' + safeUrl + '">copy link</button>' +
      '</div>' +
      '<div class="sp-url">' + safeUrl + '</div>';
  }

  function positionPopover(popover, btn) {
    var rect = btn.getBoundingClientRect();
    var popW = Math.min(230, window.innerWidth - 32);
    popover.style.width = popW + 'px';
    var left = rect.right - popW;
    if (left < 8) left = 8;
    if (left + popW > window.innerWidth - 8) left = window.innerWidth - popW - 8;
    popover.style.left = left + 'px';
    popover.style.right = 'auto';
    var spaceBelow = window.innerHeight - rect.bottom;
    if (spaceBelow >= 220) {
      popover.style.top = (rect.bottom + 6) + 'px';
      popover.style.bottom = 'auto';
    } else {
      popover.style.bottom = (window.innerHeight - rect.top + 6) + 'px';
      popover.style.top = 'auto';
    }
  }

  function closeAllPopovers() {
    document.querySelectorAll('.share-popover').forEach(function (p) {
      p.classList.remove('active');
    });
  }

  async function copyShareLink(url, btn) {
    try {
      await navigator.clipboard.writeText(url);
      var orig = btn.textContent;
      btn.textContent = 'copied!';
      setTimeout(function () { btn.textContent = orig; }, 2000);
    } catch (e) {
      prompt('Copy this link:', url);
    }
  }

  async function nativeShare(url, title) {
    try {
      await navigator.share({ title: title || 'BackcountryFinder', url: url });
    } catch (e) { /* user dismissed */ }
  }

  function openSharePopover(opts) {
    var popoverEl = opts.popoverEl;
    var anchorEl = opts.anchorEl;
    if (!popoverEl || !anchorEl) return;
    var wasOpen = popoverEl.classList.contains('active');
    closeAllPopovers();
    if (wasOpen) return;
    popoverEl.innerHTML = buildSharePopoverHTML(opts.shareUrl, opts.title, opts.headerText);
    positionPopover(popoverEl, anchorEl);
    popoverEl.classList.add('active');
  }

  document.addEventListener('click', function (e) {
    var copyBtn = e.target.closest('[data-copy-url]');
    if (copyBtn) {
      e.stopPropagation();
      copyShareLink(copyBtn.getAttribute('data-copy-url'), copyBtn);
      return;
    }
    var nativeBtn = e.target.closest('[data-native-share]');
    if (nativeBtn) {
      e.stopPropagation();
      nativeShare(nativeBtn.getAttribute('data-native-share'), nativeBtn.getAttribute('data-native-title'));
      return;
    }
    if (!e.target.closest('.share-popover')) closeAllPopovers();
  });

  // Public surface
  window.openSharePopover = openSharePopover;
  window.closeAllSharePopovers = closeAllPopovers;
})();
