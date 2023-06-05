from clubcpg.ParseBam import BamFileReadParser
import os
import logging
from multiprocessing import Pool
import numpy as np
from collections import defaultdict
import time
from pandas.core.indexes.base import InvalidIndexError


class CalculateCompleteBins:
    """
    Class to calculate the number of reads covering all CpGs
    """
    def __init__(self,
                 bam_file,fa_file, bin_size, output_directory, number_of_processors=1, mbias_read1_5=None, mbias_read1_3=None, mbias_read2_5= None, mbias_read2_3=None, no_overlap=True):
        """
        This class is initialized with a path to a bam file and a bin size
    
        :param bam_file: One of the BAM files for analysis to be performed
        :param bin_size: Size of the bins for the analysis, integer
        :number_of_processors: How many CPUs to use for parallel computation, default=1
        """
        self.input_bam_file = bam_file
        self.input_fa_file = fa_file
        self.bin_size = int(bin_size)
        self.number_of_processors = int(number_of_processors)
        self.output_directory = output_directory
        self.bins_no_reads = 0
        self.bins_yes_reads = 0
    
        self.mbias_read1_5 = mbias_read1_5
        self.mbias_read1_3 = mbias_read1_3
        self.mbias_read2_5 = mbias_read2_5
        self.mbias_read2_3 = mbias_read2_3
        self.no_overlap = no_overlap

    def calculate_bin_coverage(self, bin):
        """
        Take a single bin, return a matrix. This is passed to a multiprocessing Pool.

        :param bin: Bin should be passed as "Chr19_4343343"
        :return: pd.DataFrame with rows containing NaNs dropped
        """
        # Get reads from bam file
        parser = BamFileReadParser(self.input_bam_file,self.input_fa_file, 
                                   20, self.mbias_read1_5, self.mbias_read1_3,
                                   self.mbias_read2_5, self.mbias_read2_3, self.no_overlap)
        # Split bin into parts
        chromosome, bin_location = bin.split("_")
        bin_location = int(bin_location)
        try:
            reads = parser.parse_reads(chromosome, bin_location-self.bin_size, bin_location)
            matrix = parser.create_matrix(reads)
        except BaseException as e:
            # No reads are within this window, do nothing
            self.bins_no_reads += 1
            return None
        except:
            logging.error("Unknown error: {}".format(bin))
            return None

        # drop rows of ALL NaN
        matrix = matrix.dropna(how="all")
        # convert to data_frame of 1s and 0s, drop rows with NaN
        matrix = matrix.dropna()
        # if matrix is empty, attempt to create it with correction before giving up
        if len(matrix) == 0:
            original_matrix = matrix.copy()
            reads = parser.correct_cpg_positions(reads)
            try:
                matrix = parser.create_matrix(reads)
            except InvalidIndexError as e:
                logging.error("Invalid Index error when creating matrices at bin {}".format(bin))
                logging.debug(str(e))
                return bin, original_matrix
            except ValueError as e:
                logging.error("Matrix concat error ar bin {}".format(bin))
                logging.debug(str(e))

            matrix = matrix.dropna()
            if len(matrix) > 0:
                logging.info("Correction attempt at bin {}: SUCCESS".format(bin))
            else:
                logging.info("Correction attempt at bin {}: FAILED".format(bin))

        return bin, matrix

    def get_chromosome_lengths(self):
        """
        Get dictionary containing lengths of the chromosomes. Uses bam file for reference

        :return: Dictionary of chromosome lengths, ex: {"chrX": 222222}
        """
        parser = BamFileReadParser(self.input_bam_file, self.input_fa_file)
        return dict(zip(parser.OpenBamFile.references, parser.OpenBamFile.lengths))

    @staticmethod
    def remove_scaffolds(chromosome_len_dict):
        """
        Return a dict containing only the standard chromosomes starting with "chr"

        :param chromosome_len_dict: A dict generated by get_chromosome_lenghts()
        :return: a dict containing only chromosomes starting with "chr"
        """
        new_dict = dict(chromosome_len_dict)
        for key in chromosome_len_dict.keys():
            if not key.startswith("chr"):
                new_dict.pop(key)

        return new_dict

    def generate_bins_list(self, chromosome_len_dict):
        """
        Get a dict of lists of all bins according to desired bin size for all chromosomes in the passed dict

        :param chromosome_len_dict: A dict of chromosome length sizes from get_chromosome_lenghts, cleaned up by remove_scaffolds() if desired
        :return: dict with each key being a chromosome. ex: chr1
        """
        all_bins = defaultdict(list)
        for key, value in chromosome_len_dict.items():
            bins = list(np.arange(self.bin_size, value + self.bin_size, self.bin_size))
            bins = ["_".join([key, str(x)]) for x in bins]
            all_bins[key].extend(bins)

        return all_bins

    def analyze_bins(self, individual_chrom=None):
        """
        Main function in class. Run the Complete analysis on the data

        :param individual_chrom: Chromosome to analyze: ie "chr7"
        :return: filename of the generated report
        """

        # Track the progress of the multiprocessing and output
        def track_progress(job, update_interval=60):
            while job._number_left > 0:
                logging.info("Tasks remaining = {0}".format(
                    job._number_left * job._chunksize))
                time.sleep(update_interval)

        # Get and clean dict of chromosome lenghts, convert to list of bins
        chromosome_lengths = self.get_chromosome_lengths()
        chromosome_lengths = self.remove_scaffolds(chromosome_lengths)

        # If one chromosome was specified use only that chromosome
        if individual_chrom:
            new = dict()
            new[individual_chrom] = chromosome_lengths[individual_chrom]
            chromosome_lengths = new

        bins_to_analyze = self.generate_bins_list(chromosome_lengths)

        # Set up for multiprocessing
        # Loop over bin dict and pool.map them individually
        final_results = []
        for key in bins_to_analyze.keys():
            pool = Pool(processes=self.number_of_processors)
            results = pool.map_async(self.calculate_bin_coverage, bins_to_analyze[key])

            track_progress(results)

            # once done, get results
            results = results.get()

            final_results.extend(results)

        logging.info("Analysis complete")

        # Write to output file
        output_file = os.path.join(self.output_directory, "CompleteBins.{}.{}.csv".format(os.path.basename(self.input_bam_file), individual_chrom))

        with open(output_file, "w") as out:
            for result in final_results:
                if result:
                    # bin
                    out.write(result[0] + ",")
                    # num of reads
                    out.write(str(result[1].shape[0]) + ",")
                    # num of CpGs
                    out.write(str(result[1].shape[1]) + "\n")

        logging.info("Full read coverage analysis complete!")
        return output_file