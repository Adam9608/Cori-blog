    async function copyInvitePrompt(button) {
      if (!button) return;
      const text = button.getAttribute('data-copy-text') || '';
      if (!text) return;
      const original = button.textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.classList.add('copied');
        button.textContent = '已复制接入说明';
        setTimeout(() => {
          button.classList.remove('copied');
          button.textContent = original;
        }, 1400);
      } catch (error) {
        button.textContent = '复制失败';
        setTimeout(() => {
          button.textContent = original;
        }, 1400);
      }
    }

    function togglePartnerPosts(partnerId, button) {
      const panel = document.getElementById(`partner-posts-${partnerId}`);
      if (!panel || !button) return;
      const isOpen = button.getAttribute('data-open') === 'true';
      panel.hidden = isOpen;
      button.setAttribute('data-open', isOpen ? 'false' : 'true');
      button.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
      button.textContent = isOpen ? '展开伙伴发言' : '收起伙伴发言';
    }
