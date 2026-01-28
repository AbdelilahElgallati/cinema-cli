import fetch from 'node-fetch';
import https from 'https';

const agent = new https.Agent({
    rejectUnauthorized: false
});

const DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

/**
 * Validates a single source URL by checking if it's accessible
 * Uses HEAD request for efficiency, falls back to GET if HEAD fails
 * @param {Object} file - Source file object with file.url property
 * @param {number} timeout - Timeout in milliseconds (default: 5000)
 * @returns {Promise<Object|null>} - Returns the file object if valid, null if invalid
 */
export async function validateSource(file, timeout = 5000) {
    if (!file || !file.file || typeof file.file !== 'string') {
        return null;
    }

    const url = file.file;

    // Skip validation for localhost/proxy URLs (they're already proxied)
    if (url.includes('localhost') || url.includes('127.0.0.1')) {
        return file;
    }

    // Skip validation for URLs that are clearly invalid
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        return null;
    }

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);

        // Try HEAD first (more efficient)
        let response;
        try {
            response = await fetch(url, {
                method: 'HEAD',
                headers: {
                    'User-Agent': DEFAULT_USER_AGENT,
                    ...(file.headers || {})
                },
                agent,
                signal: controller.signal,
                redirect: 'follow'
            });
        } catch (headError) {
            // If HEAD fails, try GET with range request (for video files)
            clearTimeout(timeoutId);
            const getController = new AbortController();
            const getTimeoutId = setTimeout(() => getController.abort(), timeout);
            
            try {
                response = await fetch(url, {
                    method: 'GET',
                    headers: {
                        'User-Agent': DEFAULT_USER_AGENT,
                        'Range': 'bytes=0-1024', // Just check first 1KB
                        ...(file.headers || {})
                    },
                    agent,
                    signal: getController.signal,
                    redirect: 'follow'
                });
                clearTimeout(getTimeoutId);
            } catch (getError) {
                clearTimeout(getTimeoutId);
                return null;
            }
        }

        clearTimeout(timeoutId);

        // Consider 2xx and 3xx status codes as valid
        // Some servers return 403/401 but still serve content, so we'll be lenient
        if (response.status >= 200 && response.status < 500) {
            return file;
        }

        return null;
    } catch (error) {
        // Network errors, timeouts, etc. - consider invalid
        return null;
    }
}

/**
 * Validates multiple sources in parallel with concurrency limit
 * @param {Array<Object>} files - Array of source file objects
 * @param {number} concurrency - Maximum concurrent validations (default: 5)
 * @param {number} timeout - Timeout per source in milliseconds (default: 5000)
 * @returns {Promise<Array<Object>>} - Array of valid source file objects
 */
export async function validateSources(files, concurrency = 5, timeout = 5000) {
    if (!files || files.length === 0) {
        return [];
    }

    const results = [];
    const shouldDebug = process.argv.includes('--debug');

    if (shouldDebug) {
        console.log(`[Source Validator] Validating ${files.length} sources...`);
    }

    // Process in batches to avoid overwhelming the system
    for (let i = 0; i < files.length; i += concurrency) {
        const batch = files.slice(i, i + concurrency);
        const batchResults = await Promise.all(
            batch.map(file => validateSource(file, timeout))
        );

        const validSources = batchResults.filter(Boolean);
        results.push(...validSources);

        if (shouldDebug) {
            const validCount = validSources.length;
            const totalChecked = i + batch.length;
            console.log(`[Source Validator] Checked ${totalChecked}/${files.length} sources, ${results.length} valid so far`);
        }
    }

    if (shouldDebug) {
        console.log(`[Source Validator] Validation complete: ${results.length}/${files.length} sources are valid`);
    }

    return results;
}
