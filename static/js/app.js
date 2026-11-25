/**
 * app.js - DriveBackup Frontend
 */

// ===== Relógio no topbar =====
function atualizarRelogio() {
    const el = document.getElementById('current-time');
    if (!el) return;
    const now = new Date();
    const opts = { 
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit'
    };
    el.textContent = now.toLocaleDateString('pt-BR', opts);
}
atualizarRelogio();
setInterval(atualizarRelogio, 1000);

// ===== Proteção CSRF automática para formulários e fetch POST =====
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

(function() {
    const originalFetch = window.fetch;
    window.fetch = function(input, init) {
        init = init || {};
        const method = (init.method || 'GET').toUpperCase();
        if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
            const headers = new Headers(init.headers || {});
            if (!headers.has('X-CSRF-Token')) {
                headers.set('X-CSRF-Token', getCsrfToken());
            }
            init.headers = headers;
        }
        return originalFetch.call(this, input, init);
    };
})();

document.addEventListener('DOMContentLoaded', function() {
    const token = getCsrfToken();
    if (token) {
        document.querySelectorAll('form[method="POST"], form[method="post"]').forEach(form => {
            if (!form.querySelector('input[name="csrf_token"]')) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'csrf_token';
                input.value = token;
                form.appendChild(input);
            }
        });
    }
});

// ===== Auto-hide flash messages =====
document.addEventListener('DOMContentLoaded', function() {
    const flashes = document.querySelectorAll('.flash-message');
    flashes.forEach(flash => {
        setTimeout(() => {
            flash.style.transition = 'opacity 0.5s, transform 0.5s';
            flash.style.opacity = '0';
            flash.style.transform = 'translateY(-10px)';
            setTimeout(() => flash.remove(), 500);
        }, 5000);
    });
});

// ===== Confirmação de exclusão aprimorada =====
document.querySelectorAll('form[onsubmit]').forEach(form => {
    // já tem onsubmit inline
});

// ===== SSE Progresso (para uso futuro na página de jobs) =====
function monitorarProgresso(jobId, callback) {
    const source = new EventSource(`/api/progresso/${jobId}`);
    
    source.onmessage = function(event) {
        const data = JSON.parse(event.data);
        callback(data);
        
        if (data.etapa === 'concluido' || data.etapa === 'erro' || data.etapa === 'idle' || data.etapa === 'abortado' || data.etapa === 'timeout') {
            source.close();
        }
    };
    
    source.onerror = function() {
        source.close();
    };
    
    return source;
}
