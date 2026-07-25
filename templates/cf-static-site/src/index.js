export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const hostname = url.hostname;
    const primary = env.PRIMARY_DOMAIN;

    // Accept primary domain and www.<primary>; redirect everything else.
    if (hostname === primary || hostname === `www.${primary}`) {
      return env.ASSETS.fetch(request);
    }

    // 301 redirect to primary, preserving path + query string.
    const dest = `https://${primary}${url.pathname}${url.search}`;
    return Response.redirect(dest, 301);
  },
};
