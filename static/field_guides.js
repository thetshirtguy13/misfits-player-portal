document.querySelectorAll('.play-guide').forEach((button) => {
  button.addEventListener('click', () => {
    const number = button.dataset.guide;
    const defender = document.getElementById(`defMove${number}`);
    const ball = document.getElementById(`ballMove${number}`);
    const status = document.getElementById(`status${number}`);
    if (!defender || !ball) return;
    status.textContent = 'Ball in play — defenders are moving to their assignments.';
    defender.beginElement();
    window.setTimeout(() => ball.beginElement(), 250);
    window.setTimeout(() => { status.textContent = 'Play complete. Tap replay to watch it again.'; }, 2400);
    button.textContent = '↻ Replay scenario';
  });
});
