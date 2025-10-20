async ({ url, method, headers, body, credentials, mode, redirect, ref, timeoutMs }) => {
    const ctrl = new AbortController();
    const id = setTimeout(() => ctrl.abort("timeout"), timeoutMs);
    try {
        const init = { method, headers, credentials, mode, redirect, signal: ctrl.signal };
        if (ref) init.referrer = ref;
        if (body !== undefined && body !== null) init.body = body;

        const r = await fetch(url, init);

        // заголовки (если CORS позволит)
        const headersObj = {};
        try { r.headers.forEach((v, k) => headersObj[k.toLowerCase()] = v); } catch {}

        // --- ВАЖНО: без TypedArray! --- 
        // blob -> FileReader.readAsDataURL -> base64-пэйлоад
        let bodyB64 = null;
        try {
        const blob = await r.blob();              // уже РАСПАКОВАННОЕ тело
        bodyB64 = await new Promise((resolve) => {
            const fr = new FileReader();
            fr.onload = () => {
            const s = String(fr.result || "");
            const i = s.indexOf(",");
            resolve(i >= 0 ? s.slice(i + 1) : "");
            };
            fr.onerror = () => resolve("");
            fr.readAsDataURL(blob);
        });
        } catch {
        bodyB64 = null;                           // тело недоступно (opaque/CORS/ETP)
        }

        return {
        ok: true,
        finalUrl: r.url,
        status: r.status,
        type: r.type,            // basic|cors|opaque|opaque-redirect
        redirected: r.redirected,
        headers: headersObj,     // может быть пустым
        bodyB64,                 // base64 распакованных байтов или null
        };
    } catch (e) {
        return { ok: false, error: String(e) };
    } finally {
        clearTimeout(id);
    }
}