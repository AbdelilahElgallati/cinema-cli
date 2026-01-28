export async function getLibre(media) {
    const DOMAIN = `https://libre-subs.fifthwit.net/search?id=${media.tmdb}`;

    let url;
    if (media.type === 'movie') {
        url = `${DOMAIN}&lang=ar`;
    } else {
        url = `${DOMAIN}&season=${media.season}&episode=${media.episode}&lang=ar`;
    }
    let request = await fetch(url);

    let subtitlesWithNerdyAmountOfInformation = await request.json();

    return {
        files: [],
        subtitles: subtitlesWithNerdyAmountOfInformation.map((sub) => ({
            url: sub.url,
            lang: sub.language,
            type: sub.format
        }))
    };
}
