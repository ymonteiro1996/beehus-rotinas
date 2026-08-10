/* BeehusToken — modal global "🔑 Beehus API": cola o token Bearer (válido
   por 1 dia) que autentica as consultas migradas para a API Beehus (ver
   CLAUDE.md §8). Incluído uma única vez em base.html, disponível em toda
   página. Não é uma "tela" (§4 do CLAUDE.md) — é um widget global de sessão,
   por isso vive fora de static/js/<tela>/. */
const BeehusToken = {
  ok: false,

  /* Contexto:
     Pergunta ao backend (GET /api/beehus-token) o status do token atual —
     chamada 1x no bootstrap deste arquivo. Não retorna nada.

     Pseudocódigo:
       1. Busca o status; falha de rede -> não faz nada (botão fica no texto
          default, sem travar o carregamento da página).
       2. "Precisa colar" = sem token carregado, OU expirado (exp local), OU
          rejeitado pela API (última chamada real bateu 401/403).
       3. Atualiza o botão da masthead com o resultado. */
  verificar() {
    fetch('/api/beehus-token').then(r => r.json()).then(status => {
      const precisaColar = !status.loaded || status.expired || status.rejected;
      BeehusToken.ok = !precisaColar;
      BeehusToken.atualizarBotao();
    }).catch(() => {});
  },

  /* Contexto:
     Sincroniza o texto/cor do botão "🔑 Beehus API" da masthead com
     `BeehusToken.ok`. Não retorna nada.

     Pseudocódigo:
       1. OK -> texto neutro "Beehus API OK".
       2. Precisa colar -> texto de alerta + classe conexao-pendente. */
  atualizarBotao() {
    const label = document.getElementById('label-beehus-token');
    const btn = document.getElementById('btn-beehus-token');
    if (!label || !btn) return;
    label.textContent = BeehusToken.ok ? '🔑 Beehus API OK' : '🔑 Colar token Beehus API';
    btn.classList.toggle('conexao-pendente', !BeehusToken.ok);
  },

  /* Contexto: abre o modal onde a pessoa cola o token do dia. Chamada pelo
     clique no botão da masthead. Não retorna nada. */
  abrirModal() {
    const overlay = document.getElementById('beehus-token-overlay');
    const msg = document.getElementById('beehus-token-msg');
    msg.textContent = '';
    msg.className = 'mt-2 text-xs';
    overlay.classList.remove('hidden');
    document.getElementById('input-beehus-token').focus();
  },

  fecharModal() {
    document.getElementById('beehus-token-overlay').classList.add('hidden');
  },

  /* Contexto:
     Lê o campo do modal, valida e persiste o token (POST /api/beehus-token) —
     chamada pelo botão "Validar e salvar". Backend valida contra a API (1 GET
     barato) antes de confirmar; nunca fecha o modal em caso de erro, pra
     pessoa poder corrigir e tentar de novo. Não retorna nada.

     Pseudocódigo:
       1. Campo vazio -> mensagem de erro, sem chamar o backend.
       2. Chama POST /api/beehus-token; {error:...} (401/400) -> mensagem em
          vermelho; {warning:...} (token salvo mas não validado agora) ->
          mensagem neutra, ainda fecha o modal.
       3. Sucesso -> mensagem verde, atualiza o botão, fecha o modal. */
  salvar() {
    const input = document.getElementById('input-beehus-token');
    const msg = document.getElementById('beehus-token-msg');
    const token = (input.value || '').trim();
    msg.className = 'mt-2 text-xs';
    if (!token) {
      msg.textContent = 'Cole o token antes de salvar.';
      msg.classList.add('text-red-600');
      return;
    }
    msg.textContent = 'Validando token...';
    fetch('/api/beehus-token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        if (!ok) {
          msg.textContent = data.error || 'Falha ao validar o token.';
          msg.classList.add('text-red-600');
          return;
        }
        BeehusToken.ok = true;
        BeehusToken.atualizarBotao();
        msg.textContent = data.warning ? `Token salvo (${data.warning})` : 'Token válido — salvo com sucesso.';
        if (!data.warning) msg.classList.add('text-green-600');
        input.value = '';
        setTimeout(BeehusToken.fecharModal, 800);
      })
      .catch(() => {
        msg.textContent = 'Falha de rede ao salvar o token.';
        msg.classList.add('text-red-600');
      });
  },
};

BeehusToken.verificar();
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('input-beehus-token');
  if (input) input.addEventListener('keydown', (e) => { if (e.key === 'Enter') BeehusToken.salvar(); });
});
