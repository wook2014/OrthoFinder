# -*- coding: utf-8 -*-
"""
Created on Tue Aug  1 09:11:11 2017

@author: david

Perform directed 'reconciliation' first and then apply EggNOG method

1 - root gene trees on outgroup: unique one this time
2 - infer orthologues
"""
import os
import csv
import glob
import time
import argparse
import operator
import itertools
import Queue
import multiprocessing as mp
from collections import Counter

import tree as tree_lib
import resolve, util

def GeneToSpecies_dash(g):
  return g.split("_", 1)[0]
  
OrthoFinderIDs = GeneToSpecies_dash
  
def GeneToSpecies_secondDash(g):
  return "_".join(g.split("_", 2)[:2])
  
def GeneToSpecies_3rdDash(g):
  return "_".join(g.split("_", 3)[:3])
  
def GeneToSpecies_dot(g):
  return g.split(".", 1)[0]
  
def GeneToSpecies_hyphen(g):
  return g.split("-", 1)[0]  
    
class RootMap(object):
    def __init__(self, setA, setB, GeneToSpecies):
        self.setA = setA
        self.setB = setB
        self.GeneToSpecies = GeneToSpecies
        
    def GeneMap(self, gene_name):
        sp = self.GeneToSpecies(gene_name)
        if sp in self.setA: return True 
        elif sp in self.setB: return False
        else: raise Exception
        
def StoreSpeciesSets(t, GeneMap):
    for node in t.traverse('postorder'):
        if node.is_leaf():
            node.add_feature('sp_down', {GeneMap(node.name)})
        elif node.is_root():
            continue
        else:
            node.add_feature('sp_down', set.union(*[ch.sp_down for ch in node.get_children()]))
    for node in t.traverse('preorder'):
        if node.is_root():
            node.add_feature('sp_up', set())
        else:
            parent = node.up
            if parent.is_root():
                others = [ch for ch in parent.get_children() if ch != node]
                node.add_feature('sp_up', set.union(*[other.sp_down for other in others]))
            else:
                others = [ch for ch in parent.get_children() if ch != node]
                sp_downs = set.union(*[other.sp_down for other in others])
                node.add_feature('sp_up', parent.sp_up.union(sp_downs))
    t.add_feature('sp_down', set.union(*[ch.sp_down for ch in t.get_children()]))

def GetRoots(tree, species_tree_rooted, GeneToSpecies):
    species = set([GeneToSpecies(g) for g in tree.get_leaf_names()])
    if len(species) == 1:
        return [], 0, None
    n1, n2 = species_tree_rooted.get_children()
    t1 = set(n1.get_leaf_names())
    t2 = set(n2.get_leaf_names())
    have1 = len(species.intersection(t1)) != 0
    have2 = len(species.intersection(t2)) != 0
#    print(tree)
    while not (have1 and have2):
        # Doesn't contain outgroup, step down in species tree until it does
        if have1:
            n = n1
        else:
            n = n2
        n1, n2 = n.get_children()
        t1 = n1.get_leaf_names()
        t2 = n2.get_leaf_names()
        have1 = len(species.intersection(t1)) != 0
        have2 = len(species.intersection(t2)) != 0
        
    n1, n2 = species_tree_rooted.get_children()
    root_mapper = RootMap(t1, t2, GeneToSpecies)    
    GeneMap = root_mapper.GeneMap
    StoreSpeciesSets(tree, GeneMap)
    found = set()
    TF = set([True, False])
    TFfr = frozenset([True, False])
    Tfr = frozenset([True])
    Ffr = frozenset([False])
    fail = 0
    for m in tree:
        n = m.up
        while not n.is_root() and n.sp_down != TF:
            m = n
            n = m.up
        if n.sp_down == TF:
            children = n.get_children()
            if n.is_root():
                colour = m.sp_down
                if any([x.sp_down != colour and len(x.sp_down) == 1 for x in children]):
                    comb = Counter([frozenset(x.sp_down) for x in children])
                    # case 0
                    if comb[TFfr] == 0:
                        # case 0A - one of the branches is the root
                        for c in children:
                            if sum([c.sp_down == x.sp_down for x in children]) == 1:
                                found.add(c) # only holds for one of them
                                break
                    elif comb[TFfr] == 1 and (comb[Tfr] == 2 or comb[Ffr] == 2):
                        # case 0B - one mixed branch, two identical True/False branches
                        # we don't know this is the division, stepping down in the mixed branch might still be all same as the single state ones
                        # we'll find this division while walking up the tree
                        pass
                    elif comb[TFfr] == 1 and comb[Tfr] == 1:
                        # case 0C - one mixed branch, one True & one False
                        found.add([c for c in children if c.sp_down == TF][0])
                    else:
                        # case 0D - two mixed branches
                        # while find the transition while walking up the tree
                        pass 
#                    found.add(n)
#                    print("*** Root1 ***")
            elif len(children) == 2:
#                found.add(n)
                c1, c2 = children
                single_state = c1.sp_down if len(c1.sp_down) == 1 else c2.sp_down
                if len(c1.sp_down) == 1 and len(c2.sp_down) == 1:
                    # Case 1 - Both single state
                    if len(n.sp_up) == 1:
                        # Case 1A - 3rd clade also single state
                        # Root is the bipartition separating True from False
                        found.add(c1 if n.sp_up == c2.sp_down else c2)
                    else:
                        # Case 1B - 3rd clade is mixed
                        found.add(n)
                else:
                    # Case 2 - only one is single state and it's not the same as the 3rd clade
                    if single_state != n.sp_up:
                        # Case 2A - only one is single state and it's not the same as the 3rd clade
#                        print("*** Root3 ***")
                        found.add(c1 if len(c1.sp_down) == 1 else c2)
#                    else:
#                        # Case 2A - only one is single state and it's the same as the 3rd clade
#                        # root is in the mixed clade and will be found while walking up that
#                        pass
            else:
                fail += 1
    return list(found), fail, GeneMap  

def WriteQfO(ortho_pairs, outfilename, qForQFO = True):
    with open(outfilename, 'ab' if qForQFO else 'wb') as outfile:
        for p in ortho_pairs:
            g1, g2 = list(p)
            if qForQFO:
                g1 = g1.split("_")[-1]
                g2 = g2.split("_")[-1]
            outfile.write("%s\t%s\n" % (g1, g2))
            
def WriteQfO2(orthologues_list_pairs_list, outfilename, qForQFO = True):
    """ takes a list where each entry is a pair, (genes1, genes2), which are orthologues of one another
    """
    with open(outfilename, 'ab' if qForQFO else 'wb') as outfile:
        for gs1, gs2 in orthologues_list_pairs_list:
            for g1 in gs1:
                for g2 in gs2:
                    if qForQFO:
                        g1 = g1.split("_")[-1]
                        g2 = g2.split("_")[-1]
                    outfile.write("%s\t%s\n" % (g1, g2))
    
def GetGeneToSpeciesMap(args):
    GeneToSpecies = GeneToSpecies_dash
    if args.separator and args.separator == "dot":
        GeneToSpecies = GeneToSpecies_dot  
    elif args.separator and args.separator == "second_dash":
        GeneToSpecies = GeneToSpecies_secondDash  
    elif args.separator and args.separator == "3rd_dash":
        GeneToSpecies = GeneToSpecies_3rdDash  
    elif args.separator and args.separator == "hyphen":
        GeneToSpecies = GeneToSpecies_hyphen  
    return GeneToSpecies
   
def IsDup(node, GeneToSpecies):
    descendents = [{GeneToSpecies(l) for l in n.get_leaf_names()} for n in node.get_children()]
    try:
        return len(descendents[0].intersection(descendents[1])) != 0
    except:
        print(node)
        raise
        
#def IsDup_nonbinary(node, GeneToSpecies):
#    descendents = [{GeneToSpecies(l) for l in n.get_leaf_names()} for n in node.get_children()]
#    try:
#        for c1, c2 in itertools.combinations(descendents, 2):
#            if len(c1.intersection(c2)) != 0: return True
#        return False
#    except:
#        print(node)
#        raise
        
def GetRoot(tree, species_tree_rooted, GeneToSpecies):
        roots, j, GeneMap = GetRoots(tree, species_tree_rooted, GeneToSpecies)
#        print("%d roots" % len(roots))
        # 1. Store distance of each root from each leaf
#        for root in roots:
#            print(root)
#        return roots[0]
        if len(roots) > 0:
            root_dists = [r.get_closest_leaf()[1] for r in roots]
            i, _ = max(enumerate(root_dists), key=operator.itemgetter(1))
            return roots[i]
    
def GetOrthologues_for_tree(treeFN, species_tree_rooted, GeneToSpecies):
        qPrune=True
        orthologues = []
        if (not os.path.exists(treeFN)) or os.stat(treeFN).st_size == 0: return set(orthologues), treeFN
        try:
            tree = tree_lib.Tree(treeFN)
        except:
            tree = tree_lib.Tree(treeFN, format=3)
        if qPrune: tree.prune(tree.get_leaf_names())
        if len(tree) == 1: return set(orthologues)
        root = GetRoot(tree, species_tree_rooted, GeneToSpecies)
        if root == None: return set(orthologues), tree
        # Pick the first root for now
        if root != tree:
            tree.set_outgroup(root)

        Resolve(tree, GeneToSpecies)
        if qPrune: tree.prune(tree.get_leaf_names())
        if len(tree) == 1: return set(orthologues), tree
        for n in tree.traverse('postorder'):
            if n.is_leaf(): continue
            ch = n.get_children()
            if len(ch) == 2: 
                if not IsDup(n, GeneToSpecies):
#                    orthologues += [(l0,l1) for l0 in ch[0].get_leaf_names() for l1 in ch[1].get_leaf_names()]
                    orthologues.append((ch[0].get_leaf_names(), ch[1].get_leaf_names()))
            elif len(ch) > 2:
                species = [{GeneToSpecies(l) for l in n.get_leaf_names()} for n in ch]
                for (n0, s0), (n1, s1) in itertools.combinations(zip(ch, species), 2):
                    if len(s0.intersection(s1)) == 0:
#                        orthologues += [(l0,l1)  for l0 in n0.get_leaf_names() for l1 in n1.get_leaf_names()]
                        orthologues.append((n0.get_leaf_names(), n1.get_leaf_names()))
#        WriteQfO2(orthologues, "/home/david/projects/OrthoFinder/Development/Orthologues/ReplacingDLCpar/ExampleData/Euk/ids_branch_lengths//DendroBLAST/Orthologues_M2c_no_overlap_check_pairwise/" + os.path.split(treeFN)[1], False)
        return orthologues, tree

def AppendOrthologuesToFiles(orthologues, speciesDict, sequenceDict, iog, resultsDir):
    # Sort the orthologues according to speices pairs
    species = speciesDict.keys()
#    print(species)
    for leaves0, leaves1 in orthologues:
#        print([g.split("_")[0] for g in leaves0])
#        sys.exit()
        genes_per_species0 = [(sp, [g for g in leaves0 if g.split("_")[0] == sp]) for sp in species] 
        genes_per_species1 = [(sp, [g for g in leaves1 if g.split("_")[0] == sp]) for sp in species] 
#        print([len(x[1]) for x in genes_per_species0])
#        print([len(x[1]) for x in genes_per_species1])
#        print()        
        for sp0, genes0 in genes_per_species0:
            if len(genes0) == 0: continue
            for sp1, genes1 in genes_per_species1:
                if len(genes1) == 0: continue
                d0 = resultsDir + "Orthologues_" + speciesDict[sp0] + "/"
                d1 = resultsDir + "Orthologues_" + speciesDict[sp1] + "/"
                with open(d0 + '%s__v__%s.csv' % (speciesDict[sp0], speciesDict[sp1]), 'ab') as outfile1, open(d1 + '%s__v__%s.csv' % (speciesDict[sp1], speciesDict[sp0]), 'ab') as outfile2:
                    writer1 = csv.writer(outfile1)
                    writer2 = csv.writer(outfile2)
                    og = "OG%07d" % iog
#                    print("row")
                    writer1.writerow((og, ", ".join([sequenceDict[g] for g in genes0]), ", ".join([sequenceDict[g] for g in genes1])))
                    writer2.writerow((og, ", ".join([sequenceDict[g] for g in genes1]), ", ".join([sequenceDict[g] for g in genes0])))
            
##        o_byspeciespair = defaultdict(list)
##        for g0 in leaves0:
##            for g1 in leaves1:
##                o_byspeciespair[frozenset([g0.split("_")[0], g1.split("_")[0]])].append((g0, g1))
#        # For each pair, append the orthologues to existing files
#        for (spec1, spec2), genepairs in o_byspeciespair.items():
#            d1 = resultsDir + "Orthologues_" + speciesDict[str(spec1)] + "/"
#            d2 = resultsDir + "Orthologues_" + speciesDict[str(spec2)] + "/"
#            with open(d1 + '%s__v__%s.csv' % (speciesDict[str(spec1)], speciesDict[str(spec2)]), 'ab') as outfile1, open(d2 + '%s__v__%s.csv' % (speciesDict[str(spec2)], speciesDict[str(spec1)]), 'ab') as outfile2:
#                writer1 = csv.writer(outfile1)
#                writer2 = csv.writer(outfile2)
#                for gene1, gene2 in genepairs:
#                    og = "OG%07d" % iog
#                    writer1.writerow((og, sequenceDict[gene1], sequenceDict[gene2]))
#                    writer2.writerow((og, sequenceDict[gene2], sequenceDict[gene1]))
                          
def Resolve(tree, GeneToSpecies):
    StoreSpeciesSets(tree, GeneToSpecies)
    for n in tree.traverse("postorder"):
        resolve.resolve(n, GeneToSpecies)

def GetOrthologuesStandalone_Parallel(trees_dir, species_tree_rooted_fn, GeneToSpecies, output_dir, qSingleTree):
    species_tree_rooted = tree_lib.Tree(species_tree_rooted_fn)
    args_queue = mp.Queue()
    for treeFn in glob.glob(trees_dir + ("*" if qSingleTree else "/*")): args_queue.put((treeFn, species_tree_rooted, GeneToSpecies))
    util.RunMethodParallel(GetOrthologues_for_tree, args_queue, 8)

def Worker_WriteToFile(write_to_file_queue):
    qQueueComplete = False
    while True:
        try:
            orthologues_and_fn = write_to_file_queue.get(True, 1)
            if orthologues_and_fn == None:
                qQueueComplete = True
            orthologues, outfn = orthologues_and_fn
            WriteQfO(orthologues, outfn, False)
        except Queue.Empty:
            if qQueueComplete: 
                return
            else:
                time.sleep(1) 

def Wrapper_GetOrthologues_for_tree_and_write(treeFN, species_tree_rooted, GeneToSpecies, write_to_file_queue):
    orthologues, recon_tree = GetOrthologues_for_tree(treeFN, species_tree_rooted, GeneToSpecies)
    write_to_file_queue.put(orthologues) 

#def GetOrthologuesStandalone_Parallel(trees_dir, species_tree_rooted_fn, GeneToSpecies, output_dir, qSingleTree):
#    species_tree_rooted = tree_lib.Tree(species_tree_rooted_fn) 
#    args_queue = mp.Queue()
#    # Setup a queue for ortholgoues that need to be written   
#    write_to_file_queue = mp.Queue()
#    for treeFn in glob.glob(trees_dir + ("*" if qSingleTree else "/*")): args_queue.put((treeFn, species_tree_rooted, GeneToSpecies, write_to_file_queue))
#    # start the Writer (manager)
#    writer_proc = mp.Process(target=Worker_WriteToFile, args=(write_to_file_queue, ))
#    time.sleep(2)
#    print("Created writer process")
#    writer_proc.start()
#    time.sleep(2)
#    print("Started Writer")
#    util.RunMethodParallel(Wrapper_GetOrthologues_for_tree_and_write, args_queue, 7)
#    time.sleep(2)
#    print("Asked writer to stop")
#    writer_proc.join()
#    time.sleep(2)
#    print("Writer stopped")
    
def GetOrthologuesStandalone(trees_dir, species_tree_rooted_fn, GeneToSpecies, output_dir, qSingleTree):
    species_tree_rooted = tree_lib.Tree(species_tree_rooted_fn)
    for treeFn in glob.glob(trees_dir + ("*" if qSingleTree else "/*")):
        print("")
        print(treeFn)
        orthologues, recon_tree = GetOrthologues_for_tree(treeFn, species_tree_rooted, GeneToSpecies)
        print("%d orthologues" % len(orthologues))
        outfn = output_dir + os.path.split(treeFn)[1]
        WriteQfO(orthologues, outfn, False)

def DoOrthologuesForOrthoFinder(ogSet, treesIDsPatFn, species_tree_rooted_fn, GeneToSpecies, workingDir, output_dir, reconTreesRenamedDir):    # Create directory structure
    speciesDict = ogSet.SpeciesDict()
    SequenceDict = ogSet.SequenceDict()
    # Write directory and file structure
    speciesIDs = ogSet.speciesToUse
    nspecies = len(speciesIDs)           
    for index1 in xrange(nspecies):
        d = output_dir + "Orthologues_" + speciesDict[str(speciesIDs[index1])] + "/"
        if not os.path.exists(d): os.mkdir(d)     
        for index2 in xrange(nspecies):
            if index2 == index1: continue
            with open(d + '%s__v__%s.csv' % (speciesDict[str(speciesIDs[index1])], speciesDict[str(speciesIDs[index2])]), 'wb') as outfile:
                writer1 = csv.writer(outfile)
                writer1.writerow(("Orthogroup", speciesDict[str(speciesIDs[index1])], speciesDict[str(speciesIDs[index2])]))
    # Infer orthologues and write them to file           
    species_tree_rooted = tree_lib.Tree(species_tree_rooted_fn)
    nOgs = len(ogSet.OGs())
    for iog in xrange(nOgs):
        orthologues, recon_tree = GetOrthologues_for_tree(treesIDsPatFn(iog), species_tree_rooted, GeneToSpecies)
        util.RenameTreeTaxa(recon_tree, reconTreesRenamedDir + "OG%07d_tree.txt" % iog, ogSet.Spec_SeqDict(), qFixNegatives=True)
        AppendOrthologuesToFiles(orthologues, speciesDict, SequenceDict, iog, output_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("trees_dir")
    parser.add_argument("rooted_species_tree")
#    parser.add_argument("-p", "--prune", action='store_true')
    parser.add_argument("-s", "--separator", choices=("dot", "dash", "second_dash", "3rd_dash", "hyphen"), help="Separator been species name and gene name in gene tree taxa")
    args = parser.parse_args()
    output_dir = os.path.split(args.trees_dir)[0]
    qSingleTree = False
    try:
        tree_lib.Tree(args.trees_dir)
        qSingleTree = True
        print("Analysing single tree")
    except:
        try:
            tree = tree_lib.Tree(args.trees_dir)
            qSingleTree = True
        except:
            pass
    if not os.path.exists(output_dir):
        os.mkdir(output_dir)
    
    GeneToSpecies = GetGeneToSpeciesMap(args)
    output_dir = output_dir + "/../Orthologues_M2c/"
    print(output_dir)
    if not os.path.exists(output_dir):
        os.mkdir(output_dir)
    GetOrthologuesStandalone_Parallel(args.trees_dir, args.rooted_species_tree, GeneToSpecies, output_dir, qSingleTree)
#    GetOrthologuesStandalone(args.trees_dir, args.rooted_species_tree, GeneToSpecies, output_dir, qSingleTree)

