import axios from 'axios';
import crypto from 'crypto';
import { ErrorObject } from '../../../helpers/ErrorObject.js';

const BASE_URL = 'https://cinemaos.live';
const USER_AGENT =
    'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36';

const headers = {
    Origin: BASE_URL,
    Referer: BASE_URL,
    'User-Agent': USER_AGENT
};

const shouldDebug = process.argv.includes('--debug');

export async function getCinemaOS(params) {
    // const { tmdb } = params;
    const { tmdb, type } = params;
    if (shouldDebug) {
        console.log('CinemaOS: Starting with tmdb:', tmdb);
    }

    try {
        // 1. Get movie metadata directly (no auth needed)
        // const downloadUrl = `${BASE_URL}/api/downloadLinks?type=movie&tmdbId=${tmdb}`;
        let downloadUrl = `${BASE_URL}/api/downloadLinks?type=${type || 'movie'}&tmdbId=${tmdb}`;

        if (type === 'tv' && params.season && params.episode) {
            downloadUrl += `&season=${params.season}&episode=${params.episode}`;
        }

        if (shouldDebug) {
            console.log('CinemaOS: Requesting metadata from:', downloadUrl);
        }

        // const downloadData = (await axios.get(downloadUrl, { headers })).data
        //     .data[0];

        // console.log(
        //     'CinemaOS: Metadata received:',
        //     downloadData ? 'YES' : 'NO'
        // );

        // const releaseYear = downloadData.releaseYear;
        // const title = downloadData.movieTitle;
        // const imdbId = downloadData.subtitleLink.split('=').pop();

        const downloadData = (await axios.get(downloadUrl, { headers })).data?.data?.[0];

        if (!downloadData) {
            if (shouldDebug) {
                console.log('CinemaOS: No metadata found for this ID');
            }
            // You might want to return early or use fallbacks from 'params'
        }

        const releaseYear = downloadData?.releaseYear || params.releaseYear || '';
        const title = downloadData?.movieTitle || params.title || params.name || '';
        const imdbId = downloadData?.subtitleLink?.includes('=')
            ? downloadData.subtitleLink.split('=').pop()
            : (params.imdb || '');

        if (shouldDebug) {
            console.log(
                'CinemaOS: Extracted - Title:',
                title,
                'Year:',
                releaseYear,
                'IMDb:',
                imdbId
            );
        }

        // 2. Generate HMAC signature
        const secretKey =
            'a8f7e9c2d4b6a1f3e8c9d2b4a7f6e9c2d4b6a1f3e8c9d2b4a7f6e9c2d4b6a1f3';
        const messageString = `media|episodeId:|seasonId:|tmdbId:${tmdb}`;
        const hmacSignature = crypto
            .createHmac('sha256', secretKey)
            .update(messageString)
            .digest('hex');

        if (shouldDebug) {
            console.log('CinemaOS: Generated HMAC signature:', hmacSignature);
        }

        // 3. Get encrypted response with signature
        const apiParams = new URLSearchParams({
            type: 'movie',
            tmdbId: tmdb,
            imdbId: imdbId,
            t: title,
            ry: releaseYear,
            secret: hmacSignature
        });

        const cinemaUrl = `${BASE_URL}/api/cinemaos?${apiParams.toString()}`;
        if (shouldDebug) {
            console.log('CinemaOS: Requesting encrypted data from:', cinemaUrl);
        }

        const cinemaHeaders = {
            ...headers,
            Accept: 'application/json',
            'Content-Type': 'application/json'
        };

        const encResponse = (
            await axios.get(cinemaUrl, {
                headers: cinemaHeaders,
                timeout: 30000
            })
        ).data.data;

        if (shouldDebug) {
            console.log(
                'CinemaOS: Encrypted response received:',
                encResponse ? 'YES' : 'NO'
            );
        }

        const encryptedHex = encResponse.encrypted;
        const ivHex = encResponse.cin;
        const authTagHex = encResponse.mao;
        const saltHex = encResponse.salt;

        if (shouldDebug) {
            console.log('CinemaOS: Salt received:', saltHex ? 'YES' : 'NO');
        }

        // 4. Derive key using PBKDF2 with salt
        const password = Buffer.from(
            'a1b2c3d4e4f6588658455678901477567890abcdef1234567890abcdef123456',
            'utf8'
        );
        const salt = Buffer.from(saltHex, 'hex');

        // PBKDF2 with SHA-256, 100000 iterations, 32 bytes output (256-bit key)
        const key = crypto.pbkdf2Sync(password, salt, 100000, 32, 'sha256');

        if (shouldDebug) {
            console.log('CinemaOS: Derived decryption key');
        }

        // 5. Prepare AES-256-GCM decrypt
        const ciphertext = Buffer.from(encryptedHex, 'hex');
        const iv = Buffer.from(ivHex, 'hex');
        const authTag = Buffer.from(authTagHex, 'hex');

        const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
        decipher.setAuthTag(authTag);
        const decrypted =
            decipher.update(ciphertext, undefined, 'utf8') +
            decipher.final('utf8');

        // 6. Extract sources
        const sources = JSON.parse(decrypted).sources;
        const validEntries = Object.values(sources).filter(
            (v) => v && typeof v === 'object' && v.url
        );

        if (shouldDebug) {
            console.log('CinemaOS: Found', validEntries.length, 'valid sources');
        }

        if (!validEntries.length) {
            throw new Error('No valid sources found');
        }

        // Return all valid sources
        const files = validEntries.map((entry) => ({
            file: entry.url,
            type: 'hls',
            lang: 'en',
            headers: {
                Referer: BASE_URL,
                'User-Agent': USER_AGENT
            }
        }));

        // 7. Return in provider format
        return {
            files: files,
            subtitles: []
        };
    } catch (error) {
        // Always log errors, but keep them concise
        console.error('CinemaOS: Error:', error.message);
        if (shouldDebug && error.response) {
            console.error('CinemaOS: Status:', error.response?.status);
            console.error('CinemaOS: Data:', error.response?.data);
        }

        return new ErrorObject(
            `CinemaOS Error: ${error.message}`,
            'CinemaOS',
            500,
            'Check the implementation or server status',
            true,
            true
        );
    }
}
