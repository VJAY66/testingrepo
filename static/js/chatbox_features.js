/**
 * Modern Futuristic Chatbox - Interactive Features
 * Enhanced interactivity, animations, and UX improvements
 */

(function() {
  'use strict';

  // Debounce utility for performance
  function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
      const later = () => {
        clearTimeout(timeout);
        func(...args);
      };
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);
    };
  }

  // Throttle utility
  function throttle(func, limit) {
    let inThrottle;
    return function(...args) {
      if (!inThrottle) {
        func.apply(this, args);
        inThrottle = true;
        setTimeout(() => (inThrottle = false), limit);
      }
    };
  }

  // Smooth scroll with easing
  function smoothScroll(element, target, duration = 300) {
    const start = element.scrollTop;
    const distance = target - start;
    const startTime = performance.now();

    const easeInOutQuad = (t) => {
      return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
    };

    const scroll = (currentTime) => {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const ease = easeInOutQuad(progress);

      element.scrollTop = start + distance * ease;

      if (progress < 1) {
        requestAnimationFrame(scroll);
      }
    };

    requestAnimationFrame(scroll);
  }

  // Enhanced toast notifications with animations
  const Toast = {
    show(message, type = 'info', duration = 3000) {
      const toast = document.createElement('div');
      toast.className = 'mc-toast';
      toast.innerHTML = `
        <div class="mc-toast-content">
          <span class="mc-toast-icon">
            ${type === 'success' ? '✓' : type === 'error' ? '✕' : 'ℹ'}
          </span>
          <span class="mc-toast-message">${message}</span>
        </div>
      `;

      // Add styles if not already present
      if (!document.getElementById('mc-toast-styles')) {
        const styles = document.createElement('style');
        styles.id = 'mc-toast-styles';
        styles.textContent = `
          .mc-toast {
            position: fixed;
            bottom: 20px;
            left: 20px;
            max-width: 320px;
            padding: 12px 16px;
            background: linear-gradient(135deg, rgba(30, 30, 45, 0.95), rgba(20, 20, 35, 0.95));
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 12px;
            color: #fff;
            font-size: 13px;
            font-weight: 500;
            z-index: 9999;
            animation: toast-slide-in 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) both;
            backdrop-filter: blur(12px);
          }
          @keyframes toast-slide-in {
            from {
              opacity: 0;
              transform: translateX(-20px) scale(0.95);
            }
            to {
              opacity: 1;
              transform: translateX(0) scale(1);
            }
          }
          @keyframes toast-slide-out {
            from {
              opacity: 1;
              transform: translateX(0) scale(1);
            }
            to {
              opacity: 0;
              transform: translateX(-20px) scale(0.95);
            }
          }
          .mc-toast.hide {
            animation: toast-slide-out 0.3s cubic-bezier(0.4, 0, 0.2, 1) forwards;
          }
          .mc-toast-content {
            display: flex;
            align-items: center;
            gap: 10px;
          }
          .mc-toast-icon {
            font-size: 16px;
            font-weight: bold;
          }
          .mc-toast-message {
            flex: 1;
          }
        `;
        document.head.appendChild(styles);
      }

      document.body.appendChild(toast);

      setTimeout(() => {
        toast.classList.add('hide');
        setTimeout(() => toast.remove(), 300);
      }, duration);
    }
  };

  // Message reaction system
  const Reactions = {
    init() {
      // Add reaction emojis to messages on hover
      const messages = document.querySelectorAll('.mc-msg');
      messages.forEach(msg => {
        this.addReactionBar(msg);
      });
    },

    addReactionBar(messageEl) {
      if (messageEl.querySelector('.mc-reactions')) return;

      const reactions = document.createElement('div');
      reactions.className = 'mc-reactions';
      reactions.innerHTML = `
        <button class="mc-reaction-btn" data-reaction="👍" title="Like">👍</button>
        <button class="mc-reaction-btn" data-reaction="❤️" title="Love">❤️</button>
        <button class="mc-reaction-btn" data-reaction="😂" title="Funny">😂</button>
        <button class="mc-reaction-btn" data-reaction="🔥" title="Hot">🔥</button>
        <button class="mc-reaction-btn" data-reaction="🤔" title="Thinking">🤔</button>
      `;

      messageEl.appendChild(reactions);

      // Add event listeners
      reactions.querySelectorAll('.mc-reaction-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          const reaction = btn.dataset.reaction;
          // Here you can add API call to save reaction
          Toast.show(`Added reaction ${reaction}`, 'success', 1500);
        });
      });

      // Add styles if not already present
      if (!document.getElementById('mc-reactions-styles')) {
        const styles = document.createElement('style');
        styles.id = 'mc-reactions-styles';
        styles.textContent = `
          .mc-reactions {
            display: flex;
            gap: 4px;
            padding: 6px 8px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 20px;
            margin-top: 6px;
            opacity: 0;
            transition: all 0.2s ease;
            animation: reactions-slide-in 0.2s ease forwards;
          }
          @keyframes reactions-slide-in {
            from {
              opacity: 0;
              transform: scale(0.8);
            }
            to {
              opacity: 1;
              transform: scale(1);
            }
          }
          .mc-msg:hover .mc-reactions {
            opacity: 1;
          }
          .mc-reaction-btn {
            background: transparent;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 999px;
            padding: 4px 8px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s ease;
            line-height: 1;
          }
          .mc-reaction-btn:hover {
            background: rgba(255, 255, 255, 0.12);
            transform: scale(1.1);
          }
          .mc-reaction-btn:active {
            transform: scale(0.95);
          }
        `;
        document.head.appendChild(styles);
      }
    }
  };

  // Message delivery indicators
  const DeliveryIndicator = {
    show(messageEl) {
      const indicator = document.createElement('div');
      indicator.className = 'mc-delivery-indicator';
      indicator.innerHTML = '✓';

      messageEl.appendChild(indicator);

      // Add styles if not already present
      if (!document.getElementById('mc-delivery-styles')) {
        const styles = document.createElement('style');
        styles.id = 'mc-delivery-styles';
        styles.textContent = `
          .mc-delivery-indicator {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 16px;
            height: 16px;
            font-size: 12px;
            opacity: 0.6;
            animation: delivery-bounce 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
          }
          @keyframes delivery-bounce {
            0% {
              opacity: 0;
              transform: scale(0);
            }
            50% {
              transform: scale(1.2);
            }
            100% {
              opacity: 0.6;
              transform: scale(1);
            }
          }
        `;
        document.head.appendChild(styles);
      }
    }
  };

  // Image preview in messages
  const ImagePreview = {
    init() {
      const bubbles = document.querySelectorAll('.mc-bubble');
      bubbles.forEach(bubble => {
        const urls = this.extractImageUrls(bubble.textContent);
        if (urls.length > 0) {
          this.insertImagePreviews(bubble, urls);
        }
      });
    },

    extractImageUrls(text) {
      const urlRegex = /(https?:\/\/[^\s]+\.(?:jpg|jpeg|png|gif|webp))/gi;
      return text.match(urlRegex) || [];
    },

    insertImagePreviews(bubble, urls) {
      urls.forEach(url => {
        const img = document.createElement('img');
        img.src = url;
        img.className = 'mc-message-image';
        img.style.cssText = `
          max-width: 100%;
          max-height: 200px;
          border-radius: 8px;
          margin-top: 8px;
          cursor: pointer;
          transition: all 0.2s ease;
        `;
        img.addEventListener('click', () => this.viewFullImage(url));
        bubble.appendChild(img);
      });

      // Add styles
      if (!document.getElementById('mc-image-preview-styles')) {
        const styles = document.createElement('style');
        styles.id = 'mc-image-preview-styles';
        styles.textContent = `
          .mc-message-image:hover {
            transform: scale(1.05);
            filter: brightness(1.1);
          }
        `;
        document.head.appendChild(styles);
      }
    },

    viewFullImage(url) {
      const modal = document.createElement('div');
      modal.style.cssText = `
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.9);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 9998;
        animation: fade-in 0.2s ease;
      `;

      const img = document.createElement('img');
      img.src = url;
      img.style.cssText = `
        max-width: 90%;
        max-height: 90%;
        border-radius: 8px;
        animation: zoom-in 0.3s ease;
      `;

      modal.appendChild(img);
      modal.addEventListener('click', () => {
        modal.style.animation = 'fade-out 0.2s ease';
        setTimeout(() => modal.remove(), 200);
      });

      document.body.appendChild(modal);
    }
  };

  // Keyboard shortcuts
  const Shortcuts = {
    init() {
      document.addEventListener('keydown', (e) => {
        // Ctrl/Cmd + Enter to send message
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          const form = document.getElementById('mc-form');
          if (form && !form.dataset.submitting) {
            form.dataset.submitting = 'true';
            form.dispatchEvent(new Event('submit'));
            setTimeout(() => delete form.dataset.submitting, 500);
          }
        }
      });
    }
  };

  // Performance monitoring
  const Performance = {
    measureRender(label) {
      if (window.performance) {
        window.performance.mark(`${label}-start`);
      }
    },

    endMeasure(label) {
      if (window.performance) {
        window.performance.mark(`${label}-end`);
        try {
          window.performance.measure(label, `${label}-start`, `${label}-end`);
        } catch (e) {
          // Ignore measurement errors
        }
      }
    }
  };

  // Initialize all features when DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    // Only initialize on mobile chat
    if (window.innerWidth < 768 && document.getElementById('mc-shell')) {
      Shortcuts.init();
    }
  });

  // Export for use in other scripts
  window.ChatboxFeatures = {
    Toast,
    Reactions,
    DeliveryIndicator,
    ImagePreview,
    Shortcuts,
    Performance,
    smoothScroll,
    debounce,
    throttle
  };
})();
