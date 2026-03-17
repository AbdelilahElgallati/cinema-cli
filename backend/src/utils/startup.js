import { ErrorObject } from '../helpers/ErrorObject.js';
import dotenv from 'dotenv';
import path from 'path';

export function startup() {
  // Load .env from backend cwd first
  dotenv.config();

  // If essential keys not present, try project root .env (one level up)
  if (!process.env.TMDB_API_KEY || !process.env.BACKEND_URL) {
    const rootEnv = path.resolve(process.cwd(), '..', '.env');
    dotenv.config({ path: rootEnv });
  }

  // check required env keys
  if (!process.env.TMDB_API_KEY) {
    throw new ErrorObject(
      'Missing TMDB_API_KEY environment variable',
      'system',
      500,
      'Please set the TMDB_API_KEY environment variable',
      true,
      false
    );
  }

  // PORT is derived from BACKEND_URL when not set explicitly
  if (!process.env.PORT && process.env.BACKEND_URL) {
    try {
      const url = new URL(process.env.BACKEND_URL);
      process.env.PORT = url.port || (url.protocol === 'https:' ? '443' : '80');
    } catch (_) {
      process.env.PORT = '3010';
    }
  }
}
