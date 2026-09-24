document.addEventListener('DOMContentLoaded', function () {
  const flashEls = document.querySelectorAll('.flash');
  flashEls.forEach((flash) => {
    setTimeout(() => {
      flash.style.opacity = '0';
      flash.style.transform = 'translateY(-5px)';
      flash.style.transition = 'all 0.3s ease';
    }, 3500);
  });

  const revealCards = document.querySelectorAll('.reveal-card');
  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        revealObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.2 });
  revealCards.forEach((card) => revealObserver.observe(card));

  const countEls = document.querySelectorAll('[data-count]');
  const animateCount = (el) => {
    const target = Number(el.dataset.count);
    const suffix = el.textContent.includes('K') ? 'K' : (el.textContent.includes('+') ? '+' : '');
    const isK = target >= 1000;
    const duration = 1200;
    let start = null;

    const step = (timestamp) => {
      if (!start) start = timestamp;
      const progress = Math.min((timestamp - start) / duration, 1);
      const current = Math.floor(progress * target);
      if (isK) {
        const value = (current / 1000).toFixed(1);
        el.textContent = `${value}K${suffix}`;
      } else {
        el.textContent = `${current}${suffix}`;
      }
      if (progress < 1) requestAnimationFrame(step);
    };

    requestAnimationFrame(step);
  };

  const countObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        animateCount(entry.target);
        countObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });
  countEls.forEach((el) => countObserver.observe(el));

  const searchInput = document.querySelector('input[name="q"]');
  if (searchInput) {
    searchInput.addEventListener('focus', function () {
      this.parentElement.style.boxShadow = '0 0 0 4px rgba(37, 99, 235, 0.08)';
    });
    searchInput.addEventListener('blur', function () {
      this.parentElement.style.boxShadow = 'none';
    });
  }

  const menuToggle = document.querySelector('.menu-toggle');
  const sidebar = document.querySelector('.sidebar');
  if (menuToggle && sidebar) {
    menuToggle.addEventListener('click', () => sidebar.classList.toggle('is-open'));
    document.addEventListener('click', (event) => {
      if (window.innerWidth <= 920 && !sidebar.contains(event.target) && !menuToggle.contains(event.target)) {
        sidebar.classList.remove('is-open');
      }
    });
  }

  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && searchInput) {
      event.preventDefault();
      searchInput.focus();
    }
  });
});
