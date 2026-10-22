// Expand every significant digit in the stored number, including tiny errors.
// Exact zero stays zero; formatting never invents noise or extra precision.
function fullDecimal(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const source = String(value);
  if (!/[eE]/.test(source)) return source;
  const [coefficient, power] = source.toLowerCase().split('e');
  const negative = coefficient.startsWith('-');
  const unsigned = negative ? coefficient.slice(1) : coefficient;
  const [integer, fraction = ''] = unsigned.split('.');
  const digits = integer + fraction;
  const point = integer.length + Number(power);
  const expanded = point <= 0 ? '0.' + '0'.repeat(-point) + digits
    : point >= digits.length ? digits + '0'.repeat(point - digits.length)
    : digits.slice(0, point) + '.' + digits.slice(point);
  return (negative ? '-' : '') + expanded;
}
