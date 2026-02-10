import { ErrorObject } from '../helpers/ErrorObject.js';
import dotenv from 'dotenv';
import path from 'path';

export function startup() {
  // Load .env from backend cwd first
  dotenv.config();

  // If essential keys not present, try project root .env (one level up)
  if (!process.env.TMDB_API_KEY || !process.env.PORT) {
    const rootEnv = path.resolve(process.cwd(), '..', '.env');
    dotenv.config({ path: rootEnv });
  }

  // check required env keys
  const TMDB_API_KEY =
    process.env.TMDB_API_KEY ||
    (() => {
      throw new ErrorObject(
        'Missing TMDB_API_KEY environment variable',
        'system',
        500,
        'Please set the TMDB_API_KEY environment variable',
        true,
        false
      );
    });

  // No longer strictly enforcing PORT check here as index.js handles fallback/default
  /*
  const PORT = process.env.PORT;
  if (!PORT) {
    throw new ErrorObject(
      'Missing PORT environment variable',
      'system',
      500,
      'Please set the PORT environment variable',
      true,
      false
    );
  }
  */
}
