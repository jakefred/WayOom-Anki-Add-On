export const OCR_LANGUAGES = Object.freeze({
  eng: 'English', spa: 'Spanish', fra: 'French', deu: 'German',
  por: 'Portuguese', ita: 'Italian', rus: 'Russian', jpn: 'Japanese',
  kor: 'Korean', chi_sim: 'Chinese (simplified)', chi_tra: 'Chinese (traditional)',
  ara: 'Arabic', hin: 'Hindi',
});

export function normalizeOcrLanguages(languages = ['eng']) {
  if (!Array.isArray(languages) || ![1, 2].includes(languages.length)
      || languages.some(code => !Object.hasOwn(OCR_LANGUAGES, code))
      || (languages.length === 2 && (languages[0] === 'eng' || languages[1] !== 'eng'))) {
    throw new Error('Choose a supported OCR language.');
  }
  return [...languages];
}

export function ocrLanguageLabel(languages) {
  return normalizeOcrLanguages(languages).map(code => OCR_LANGUAGES[code]).join(' + ');
}
