const illustrations: Record<string, string> = {
  'FORD|F-150': '/vehicles/ford-f-150.webp',
  'CHEVROLET|SILVERADO 1500': '/vehicles/chevrolet-silverado-1500.webp',
  'RAM|1500': '/vehicles/ram-1500.webp',
  'TOYOTA|RAV4': '/vehicles/toyota-rav4.webp',
  'HONDA|CIVIC': '/vehicles/honda-civic.webp',
  'HONDA|CR-V': '/vehicles/honda-cr-v.webp',
  'TOYOTA|CAMRY': '/vehicles/toyota-camry.webp',
  'NISSAN|ROGUE': '/vehicles/nissan-rogue.webp',
};

const crossoverMakes = new Set([
  'FORD|ESCAPE',
  'CHEVROLET|EQUINOX',
  'TOYOTA|HIGHLANDER',
]);

const sedanMakes = new Set([
  'CHEVROLET|MALIBU',
  'HONDA|ACCORD',
  'NISSAN|ALTIMA',
]);

const makeThumbnails: Record<string, string> = {
  CHEVROLET: '/vehicles/thumbs/chevrolet-silverado-1500.webp',
  FORD: '/vehicles/thumbs/ford-f-150.webp',
  HONDA: '/vehicles/thumbs/honda-civic.webp',
  HYUNDAI: '/vehicles/thumbs/hyundai-crossover.webp',
  JEEP: '/vehicles/thumbs/jeep-suv.webp',
  NISSAN: '/vehicles/thumbs/nissan-rogue.webp',
  RAM: '/vehicles/thumbs/ram-1500.webp',
  SUBARU: '/vehicles/thumbs/subaru-crossover.webp',
  TOYOTA: '/vehicles/thumbs/toyota-rav4.webp',
  VOLKSWAGEN: '/vehicles/thumbs/volkswagen-sedan.webp',
};

export function getMakeThumbnail(make: string): string {
  return makeThumbnails[make.toUpperCase()] ?? '/vehicles/thumbs/honda-cr-v.webp';
}

/**
 * Editorial artwork is deliberately non-official. A body-style fallback means
 * every report keeps its visual hierarchy while the illustration library grows.
 */
export function getVehicleIllustration(make: string, model: string): string {
  const key = `${make.toUpperCase()}|${model.toUpperCase()}`;

  if (illustrations[key]) return illustrations[key];
  if (crossoverMakes.has(key)) return '/vehicles/toyota-rav4.webp';
  if (sedanMakes.has(key)) return '/vehicles/toyota-camry.webp';
  return '/vehicles/honda-cr-v.webp';
}
