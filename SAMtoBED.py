#!/usr/bin/python

# John M. Gaspar (jsh58@wildcats.unh.edu)
# May 2017

# This script converts a SAM file to BED format.
#   It combines "properly paired" alignments into
#   a single BED interval.  There are options to
#   include unpaired alignments in the output,
#   and they can be extended to a specified or a
#   calculated length.
# The input SAM can be in any sort order, or
#   unsorted.  A BAM can be piped in via
#   'samtools view', e.g.:
# $ samtools view -h <BAM> | python SAMtoBED.py -i - -o <BED>

import sys
import gzip
import re
version = '0.1'
copyright = 'Copyright (C) 2017 John M. Gaspar (jsh58@wildcats.unh.edu)'

def printVersion():
  sys.stderr.write('SAMtoBED.py, version %s\n' % version)
  sys.stderr.write(copyright + '\n')
  sys.exit(-1)

def usage():
  sys.stderr.write('''Usage: python SAMtoBED.py  [options]  -i <input>  -o <output>
    -i <input>    SAM alignment file (can be in any sort order,
                    or unsorted; use '-' for stdin)
    -o <output>   Output BED file
  Options for unpaired alignments:
    -n            Do not print unpaired alignments (default)
    -y            Print unpaired alignments
    -a <int>      Print unpaired alignments, with fragment length
                    increased to specified value
    -x            Print unpaired alignments, with fragment length
                    increased to average value calculated from
                    paired alignments
  Other options:
    -s            Option to produce sorted output
    -t <file>     Produce a file summarizing fragment lengths
    -v            Run in verbose mode
''')
  sys.exit(-1)

def openRead(filename):
  '''
  Open filename for reading. '-' indicates stdin.
    '.gz' suffix indicates gzip compression.
  '''
  if filename == '-':
    return sys.stdin
  try:
    if filename[-3:] == '.gz':
      f = gzip.open(filename, 'rb')
    else:
      f = open(filename, 'rU')
  except IOError:
    sys.stderr.write('Error! Cannot read input file %s\n' % filename)
    sys.exit(-1)
  return f

def openWrite(filename):
  '''
  Open filename for writing. '-' indicates stdout.
    '.gz' suffix indicates gzip compression.
  '''
  if filename == '-':
    return sys.stdout
  try:
    if filename[-3:] == '.gz':
      f = gzip.open(filename, 'wb')
    else:
      f = open(filename, 'w')
  except IOError:
    sys.stderr.write('Error! Cannot write to output file %s\n' % filename)
    sys.exit(-1)
  return f

def getInt(arg):
  '''
  Convert given argument to int.
  '''
  try:
    val = int(arg)
  except ValueError:
    sys.stderr.write('Error! Cannot convert %s to int\n' % arg)
    sys.exit(-1)
  return val

def loadChrLen(line, chr, chrOrder):
  '''
  Load chromosome lengths from the SAM file header.
  '''
  mat = re.search(r'@SQ\s+SN:(\S+)\s+LN:(\d+)', line)
  if mat:
    chr[mat.group(1)] = int(mat.group(2))
    chrOrder.append(mat.group(1))

def parseCigar(cigar, length):
  '''
  Determine distance to 3' end of aligned fragment
    (accounting for D/I/S in CIGAR).
  '''
  ops = re.findall(r'(\d+)([DIS])', cigar)
  for op in ops:
    if op[1] == 'D':
      length += int(op[0])
    else:
      length -= int(op[0])
  return length

def writeOut(fOut, ref, start, end, read, chr, verbose):
  '''
  Write BED output. Adjust any read that extends beyond
    chromosome ends.
  '''
  if start < 0:
    start = 0
    if verbose:
      sys.stderr.write('Warning! Read %s prevented ' % read \
        + 'from extending below 0 on %s\n' % ref)
  if ref in chr and end > chr[ref]:
    end = chr[ref]
    if verbose:
      sys.stderr.write('Warning! Read %s prevented ' % read \
        + 'from extending past %d on %s\n' % (chr[ref], ref))
  fOut.write('%s\t%d\t%d\t%s\n' % (ref, start, end, read))

def writeSorted(fOut, res, chrOrder, chr, verbose):
  '''
  Sort output.
  '''
  if not chrOrder:
    chrOrder = sorted(res.keys())
  for chrom in chrOrder:
    for k in sorted(res[chrom]):
      writeOut(fOut, chrom, k[0], k[1], k[2], chr, verbose)

def checkPaired(pos, verbose):
  '''
  Check if any paired alignments weren't processed.
  '''
  unpaired = 0
  for r in pos:
    if pos[r] >= 0:
      if verbose:
        sys.stderr.write('Warning! Read %s missing its pair\n' % r)
      unpaired += 1
  return unpaired

def saveResult(res, chrom, start, end, header, chr, verbose):
  '''
  Save BED record to dict, for later sorting.
  '''
  if chrom not in res:
    res[chrom] = list()
  if start < 0:
    start = 0
    if verbose:
      sys.stderr.write('Warning! Read %s prevented ' % header \
        + 'from extending below 0 on %s\n' % chrom)
  if chrom in chr and end > chr[chrom]:
    end = chr[chrom]
    if verbose:
      sys.stderr.write('Warning! Read %s prevented ' % header \
        + 'from extending past %d on %s\n' % (chr[chrom], chrom))
  res[chrom].append((start, end, header))

def processPaired(header, chrom, rc, start, offset, pos,
    chr, fOut, extendOpt, length, sortOpt, res, verbose):
  '''
  Process a properly paired SAM record. If first, save end
    position to pos dict; if second, write complete record.
  '''
  # 2nd of PE reads
  if header in pos:
    if pos[header] < 0:
      sys.stderr.write('Error! Read %s already analyzed\n' % header)
      sys.exit(-1)

    # save end position
    if rc:
      start += offset

    # save/write result
    if sortOpt:
      saveResult(res, chrom, min(start, pos[header]), \
        max(start, pos[header]), header, chr, verbose)
    else:
      writeOut(fOut, chrom, min(start, pos[header]), \
        max(start, pos[header]), header, chr, verbose)

    # keep track of fragment lengths
    dist = abs(start - pos[header])
    length[dist] = length.get(dist, 0) + 1

    pos[header] = -1  # records that read was processed

  # 1st of PE reads: save end position
  else:
    if rc:
      pos[header] = start + offset
    else:
      pos[header] = start

def processUnpaired(header, chrom, rc, start, offset, pos,
    chr, fOut, addBP, sortOpt, res, verbose):
  '''
  Process an unpaired SAM record.
  '''
  # extend 3' end of read (to total length 'addBP')
  end = start + offset
  if addBP != 0:
    if rc:
      start = min(end - addBP, start)
    else:
      end = max(start + addBP, end)

  # save/write result
  if sortOpt:
    saveResult(res, chrom, start, end, header, chr, verbose)
  else:
    writeOut(fOut, chrom, start, end, header, chr, verbose)
  pos[header] = -2  # records that read was processed

def processSingle(single, pos, chr, fOut, addBP,
    sortOpt, res, verbose):
  '''
  Process saved singletons (unpaired alignments)
    using calculated extension size.
  '''
  if addBP == 0:
    sys.stderr.write('Error! Cannot calculate fragment ' \
      + 'lengths: no paired alignments\n')
    sys.exit(-1)

  # process reads
  count = 0
  for header in single:
    for idx in range(len(single[header])):
      chrom, rc, start, offset = single[header][idx]
      processUnpaired(header, chrom, rc, start, offset,
        pos, chr, fOut, addBP, sortOpt, res, verbose)
      count += 1
  return count

def parseSAM(fIn, fOut, singleOpt, addBP, extendOpt,
    sortOpt, histFile, verbose):
  '''
  Parse the input file, and produce the output file.
  '''
  chr = {}      # chromosome lengths
  pos = {}      # position of first alignment (for paired alignments)
  single = {}   # to save unpaired alignments (for calc.-extension option)
  count = 0     # count of unpaired alignments
  length = {}   # to save fragment lengths
  res = {}      # to save results, for sorted output
  chrOrder = [] # to save chromosome order, for sorted output

  line = fIn.readline().rstrip()
  while line:

    # skip header
    if line[0] == '@':
      loadChrLen(line, chr, chrOrder)  # load chromosome length
      line = fIn.readline().rstrip()
      continue

    # save flag and start position
    spl = line.split('\t')
    if len(spl) < 11:
      sys.stderr.write('Error! Poorly formatted SAM record:\n' \
        + line)
      sys.exit(-1)
    flag = getInt(spl[1])
    start = getInt(spl[3]) - 1

    # skip unmapped, secondary, and supplementary
    if flag & 0x904:
      line = fIn.readline().rstrip()
      continue

    # process alignment
    offset = parseCigar(spl[5], len(spl[9]))
    if flag & 0x2:
      # properly paired alignment
      processPaired(spl[0], spl[2], flag & 0x10, start, offset,
        pos, chr, fOut, extendOpt, length, sortOpt, res, verbose)

    elif extendOpt:
      # with calculated-extension option, save unpaired alignments
      #   until after extension length is calculated
      if spl[0] in single:
        single[spl[0]].append((spl[2], flag & 0x10, start, offset))
      else:
        single[spl[0]] = [(spl[2], flag & 0x10, start, offset)]
      pos[spl[0]] = -2  # records that read was processed

    elif singleOpt:
      # process singletons directly (w/o extendOpt)
      processUnpaired(spl[0], spl[2], flag & 0x10, start,
        offset, pos, chr, fOut, addBP, sortOpt, res, verbose)
      count += 1

    line = fIn.readline().rstrip()

  # check for paired alignments that weren't processed
  unpaired = checkPaired(pos, verbose)

  # sum paired fragment lengths
  countPE = 0
  lenPE = 0.0
  for n in length:
    countPE += length[n]
    lenPE += n * length[n]

  # for calculated-extension option, process saved unpaired alns
  if extendOpt:
    if countPE:
      addBP = int(round(lenPE / countPE))
    count = processSingle(single, pos, chr, fOut, addBP,
      sortOpt, res, verbose)

  # produce sorted output
  if sortOpt:
    writeSorted(fOut, res, chrOrder, chr, verbose)

  # produce histogram file of fragment lengths
  if histFile != None and length:
    for i in range(max(length.keys()) + 1):
      histFile.write(str(i) + '\t' + str(length.get(i, 0)) + '\n')

  # log counts
  if verbose:
    sys.stderr.write('Paired alignments (fragments): ' \
      + '%d (%d)\n' % (countPE*2, countPE))
    if countPE:
      sys.stderr.write('  Average fragment length: %.1fbp\n' \
        % ( lenPE / countPE ))
    if unpaired:
      sys.stderr.write('"Paired" reads missing mates: %d\n' % unpaired)
    if singleOpt or extendOpt:
      sys.stderr.write('Unpaired alignments: %d\n' % count)
      if addBP:
        sys.stderr.write('  (extended to length %dbp)\n' % addBP)

def main():
  '''
  Main.
  '''
  # Default parameters
  infile = None      # input file
  outfile = None     # output file
  singleOpt = False  # option to print unpaired alignments
  addBP = 0          # number of bp to add to unpaired reads
  extendOpt = False  # option to calculate extension size
  sortOpt = False    # option to produce sorted output
  histFile = None    # output file for histogram of fragment lengths
  verbose = False    # verbose option

  # get command-line args
  args = sys.argv[1:]
  i = 0
  while i < len(args):
    if args[i] == '-h' or args[i] == '--help':
      usage()
    elif args[i] == '--version':
      printVersion()
    elif args[i] == '-v':
      verbose = True
    elif args[i] == '-n':
      singleOpt = False
    elif args[i] == '-y':
      singleOpt = True
    elif args[i] == '-x':
      extendOpt = True
    elif args[i] == '-s':
      sortOpt = True
    elif i < len(args) - 1:
      if args[i] == '-i':
        infile = openRead(args[i+1])
      elif args[i] == '-o':
        outfile = openWrite(args[i+1])
      elif args[i] == '-a':
        singleOpt = True
        addBP = max(getInt(args[i+1]), 0)
      elif args[i] == '-t':
        histFile = openWrite(args[i+1])
      else:
        sys.stderr.write('Error! Unknown parameter: %s\n' % args[i])
        usage()
      i += 1
    else:
      sys.stderr.write('Error! Unknown parameter with no arg: ' \
        + '%s\n' % args[i])
      usage()
    i += 1

  # check for I/O errors
  if infile == None or outfile == None:
    sys.stderr.write('Error! Must specify input and output files\n')
    usage()

  # process files
  parseSAM(infile, outfile, singleOpt, addBP, extendOpt,
    sortOpt, histFile, verbose)
  if infile != sys.stdin:
    infile.close()
  if histFile != None and histFile != sys.stdout:
    histFile.close()
  if outfile != sys.stdout:
    outfile.close()

if __name__ == '__main__':
  main()
