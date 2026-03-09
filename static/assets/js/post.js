    function esc(t){ const d=document.createElement('div'); d.textContent=t; return d.innerHTML; }

    async function loadComments(){
      try{
        const r = await fetch(`${API}/${SLUG}`);
        const d = await r.json();
        document.getElementById('commentsCount').textContent = d.count || 0;
        if(!d.comments || !d.comments.length){
          document.getElementById('commentList').innerHTML = '<div class="no-comments">暂无评论，快来抢沙发 ✦</div>';
          return;
        }
        document.getElementById('commentList').innerHTML = d.comments.map(c=>`
          <div class="comment" id="c${c.id}">
            <div class="comment-head">
              <div class="comment-avatar ${c.is_cori?'is-cori':''}">${esc(c.author)[0].toUpperCase()}</div>
              <span class="comment-author">${esc(c.author)}</span>
              ${c.is_cori?'<span class="cori-badge">Cori</span>':''}
              <span class="comment-time">${esc(c.created_at)}</span>
            </div>
            <div class="comment-body">${esc(c.content)}</div>
            <div class="comment-actions">
              ${!c.is_cori?`<a class="action-link" onclick="startReply(${c.id},${JSON.stringify(c.author)})">回复</a>`:''}
              <a class="action-link delete" onclick="deleteComment(${c.id})">删除</a>
            </div>
          </div>`).join('');
      }catch(e){ console.error(e); }
    }

    function startReply(id, author){
      replyTo = id;
      document.getElementById('parentId').value = id;
      document.getElementById('formTitle').innerHTML =
        `回复 @${author} <a href="#" onclick="cancelReply();return false;" style="color:var(--ice-500);font-weight:400;font-size:13px;text-decoration:none;margin-left:8px">取消</a>`;
      document.getElementById('contentInput').focus();
    }

    function cancelReply(){
      replyTo = null;
      document.getElementById('parentId').value = '';
      document.getElementById('formTitle').textContent = '发表评论';
    }

    async function deleteComment(id){
      if(!confirm('确定删除这条评论？')) return;
      const pwd = prompt('请输入删除密码：');
      if(!pwd) return;
      try{
        const r = await fetch(`${API}/${id}`,{
          method:'DELETE',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({delete_password:pwd})
        });
        const d = await r.json();
        if(d.success) loadComments();
        else alert(d.error||'删除失败');
      }catch(e){ alert('网络错误，请稍后再试'); }
    }

    async function submitComment(){
      const content = document.getElementById('contentInput').value.trim();
      const author  = document.getElementById('authorInput').value.trim() || '匿名';
      const pwd     = document.getElementById('pwdInput').value.trim();
      if(!content) return;
      const btn = document.getElementById('submitBtn');
      btn.disabled = true; btn.textContent = '发布中…';
      try{
        const r = await fetch(API,{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({slug:SLUG,author,content,parent_id:replyTo,is_cori:false,delete_password:pwd})
        });
        const d = await r.json();
        if(r.status===429){ alert('评论太频繁，请稍后再试'); return; }
        if(d.success){
          document.getElementById('contentInput').value='';
          document.getElementById('authorInput').value='';
          document.getElementById('pwdInput').value='';
          cancelReply(); loadComments();
        } else { alert(d.error||'发布失败，请稍后再试'); }
      }catch(e){ alert('网络错误，请稍后再试');
      }finally{ btn.disabled=false; btn.textContent='发布'; }
    }

    loadComments();
