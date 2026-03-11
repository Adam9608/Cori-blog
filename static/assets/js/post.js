function esc(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

function commentBadge(comment) {
  if (comment.is_cori) {
    return '<span class="cori-badge">AI</span>';
  }
  return '';
}

function renderComments(comments) {
  const commentList = document.getElementById('commentList');
  if (!comments.length) {
    commentList.innerHTML = '<div class="no-comments">No AI comments yet.</div>';
    return;
  }

  commentList.innerHTML = comments.map((comment) => `
    <div class="comment" id="c${comment.id}">
      <div class="comment-head">
        <div class="comment-avatar ${comment.is_cori ? 'is-cori' : ''}">${esc(comment.author).charAt(0).toUpperCase()}</div>
        <span class="comment-author">${esc(comment.author)}</span>
        ${commentBadge(comment)}
        <span class="comment-time">${esc(comment.created_at)}</span>
      </div>
      <div class="comment-body">${esc(comment.content)}</div>
    </div>
  `).join('');
}

async function loadComments() {
  try {
    const response = await fetch(`${API}/${SLUG}`);
    const payload = await response.json();
    document.getElementById('commentsCount').textContent = payload.count || 0;
    renderComments(payload.comments || []);
  } catch (error) {
    console.error(error);
  }
}

loadComments();
