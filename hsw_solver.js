const puppeteer = require('puppeteer-core');
const { execSync } = require('child_process');
const fs = require('fs');

const chromiumPath = execSync('which chromium').toString().trim();

const HSW_CACHE_DIR = '/tmp/hsw_cache';
const HSW_CACHE_AGE = 3600 * 1000;

function getCachePath(scriptPath) {
    const crypto = require('crypto');
    const hash = crypto.createHash('md5').update(scriptPath).digest('hex').slice(0, 12);
    return `${HSW_CACHE_DIR}/hsw_${hash}.js`;
}

async function getHSWScript(page, scriptPath) {
    if (!fs.existsSync(HSW_CACHE_DIR)) {
        fs.mkdirSync(HSW_CACHE_DIR, { recursive: true });
    }

    const cachePath = getCachePath(scriptPath);
    if (fs.existsSync(cachePath)) {
        const stat = fs.statSync(cachePath);
        if (Date.now() - stat.mtimeMs < HSW_CACHE_AGE) {
            return fs.readFileSync(cachePath, 'utf8');
        }
    }

    const hswUrl = `https://newassets.hcaptcha.com${scriptPath}/hsw.js`;
    const script = await page.evaluate(async (url) => {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`fetch ${resp.status}`);
        return resp.text();
    }, hswUrl);

    fs.writeFileSync(cachePath, script);
    return script;
}

async function solveHSW(reqJwt) {
    let browser;
    try {
        browser = await puppeteer.launch({
            executablePath: chromiumPath,
            headless: 'new',
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--single-process',
                '--no-zygote',
                '--disable-extensions',
                '--disable-background-networking',
            ],
            timeout: 20000,
        });

        const page = await browser.newPage();
        await page.goto('about:blank');

        const jwtParts = reqJwt.split('.');
        const padded = jwtParts[1].replace(/-/g, '+').replace(/_/g, '/');
        const payload = JSON.parse(Buffer.from(padded, 'base64').toString());
        const scriptPath = payload.l;

        const hswScript = await getHSWScript(page, scriptPath);

        const token = await page.evaluate(async (script, jwt) => {
            eval(script);
            if (typeof hsw === 'function') return await hsw(jwt);
            throw new Error('hsw function not found');
        }, hswScript, reqJwt);

        return token;
    } finally {
        if (browser) await browser.close().catch(() => {});
    }
}

const mode = process.argv[2];

if (mode === 'solve') {
    const reqJwt = process.argv[3];
    if (!reqJwt) process.exit(1);
    solveHSW(reqJwt).then(t => {
        process.stdout.write(t);
    }).catch(e => {
        process.stderr.write(e.message || String(e));
        process.exit(1);
    });
} else if (mode === 'full') {
    const inputFile = process.argv[3] || '/tmp/hsw_input.json';
    const outputFile = process.argv[4] || '/tmp/hsw_output.json';

    const input = JSON.parse(fs.readFileSync(inputFile, 'utf8'));
    solveHSW(input.req).then(token => {
        fs.writeFileSync(outputFile, JSON.stringify({ token, req: input.req }));
        process.stdout.write('OK');
    }).catch(e => {
        fs.writeFileSync(outputFile, JSON.stringify({ error: e.message, req: input.req }));
        process.stderr.write(e.message);
        process.exit(1);
    });
} else {
    const reqJwt = mode;
    if (!reqJwt) process.exit(1);
    solveHSW(reqJwt).then(t => {
        process.stdout.write(t);
    }).catch(e => {
        process.stderr.write(e.message || String(e));
        process.exit(1);
    });
}
