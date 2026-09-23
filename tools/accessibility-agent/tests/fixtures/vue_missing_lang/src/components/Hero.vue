<template>
  <!-- BUG: img element missing alt attribute (WCAG 1.1.1) -->
  <div class="hero-section">
    <img src="/images/hero.jpg" class="hero-image" />
    <h1 class="hero-title">Welcome to our platform</h1>

    <!-- BUG: Button missing accessible name — icon-only, no text or aria-label -->
    <button class="hero-cta" @click="handleCTA">
      <svg class="icon-arrow" viewBox="0 0 24 24">
        <path d="M5 12h14M12 5l7 7-7 7" />
      </svg>
    </button>
  </div>

  <nav class="primary-nav">
    <!-- BUG: Link with no meaningful text (WCAG 2.4.4) -->
    <a href="/products" class="nav-link">Click here</a>
    <a href="/about" class="nav-link">Read more</a>
  </nav>
</template>

<script>
export default {
  name: "HeroSection",
  methods: {
    handleCTA() {
      this.$router.push("/products");
    },
  },
};
</script>

<style scoped>
.hero-section {
  display: flex;
  flex-direction: column;
  align-items: center;
}

/* BUG: Removes focus ring without replacement (WCAG 2.4.7) */
.hero-cta:focus {
  outline: none;
}
</style>
