import os, sys
import glob
import time

import numpy as np
import torch
import json
import nltk
import argparse
import fnmatch
import random

from transformers import AlbertTokenizer
from transformers import BertTokenizer
import pdb

option_num = {"A": 0, "B": 1, "C": 2, "D": 3}

def get_json_file_list(data_dir):
    files = []
    for root, dir_names, file_names in os.walk(data_dir):
        for filename in fnmatch.filter(file_names, '*.json'):
            files.append(os.path.join(root, filename))
    return files

def tokenize_ops(ops, tokenizer):
    ret = []
    for i in range(4):
        ret.append(tokenizer.tokenize(ops[i]))
    return ret

def to_device(L, device):
    if (type(L) != list):
        return L.to(device)
    else:
        ret = []
        for item in L:
            ret.append(to_device(item, device))
        return ret

class ClothSample(object):
    def __init__(self):
        self.article = None
        self.ph = []
        self.ops = []
        self.ans = []
        self.high = 0

    def convert_tokens_to_ids(self, tokenizer):
        self.article = tokenizer.convert_tokens_to_ids(self.article)
        self.article = tokenizer.build_inputs_with_special_tokens(self.article)
        self.article = torch.Tensor(self.article)
        for i in range(len(self.ops)):
            for k in range(4):
                self.ops[i][k] = tokenizer.convert_tokens_to_ids(self.ops[i][k])
                self.ops[i][k] = torch.Tensor(self.ops[i][k])
        self.ph = torch.Tensor(self.ph)
        self.ans = torch.Tensor(self.ans)
                
        
class Preprocessor(object):
    def __init__(self, args, device='cpu'):
        # self.tokenizer = BertTokenizer.from_pretrained(args.bert_model)
        self.tokenizer = AlbertTokenizer.from_pretrained(args.bert_model)
        self.data_dir = args.data_dir
        file_list = get_json_file_list(args.data_dir)
        self.data = []
        #max_article_len = 0
        for file_name in file_list:
            data = json.loads(open(file_name, 'r').read())
            data['file_name'] = None
            if('test' in file_name):
                data['file_name'] = file_name[17:]
            self.data.append(data)
            #max_article_len = max(max_article_len, len(nltk.word_tokenize(data['article'])))
        self.data_objs = []
        #print('ok')
        cnt=0
        for sample in self.data:
            cnt=cnt+1
            self.data_objs += self._create_sample(sample)
            #break
        print('sample cnts:',cnt)
        for i in range(len(self.data_objs)):
            self.data_objs[i].convert_tokens_to_ids(self.tokenizer)
            #break
        torch.save(self.data_objs, args.save_name)
        
    def _create_sample(self, data):
            shi = 0
            cnt = 0
            article = self.tokenizer.tokenize(data['article'])
            if (len(article) <= 512):
                sample = ClothSample()
                sample.article = article
                if data["file_name"]:
                    sample.file_name = data["file_name"]
                for p in range(len(article)):
                    if (sample.article[p] == '_'):
                        sample.article[p] = '[MASK]'
                        sample.ph.append(p)
                        ops = tokenize_ops(data['options'][cnt], self.tokenizer)
                        sample.ops.append(ops)
                        if data['answers']:
                            sample.ans.append(ord(data['answers'][cnt]) - ord('A'))
                        cnt += 1
                return [sample]
            else:
                first_sample = ClothSample()
                second_sample = ClothSample()
                second_s = len(article) - 512
                if data["file_name"]:
                    first_sample.file_name = data["file_name"]
                    second_sample.file_name = data["file_name"]
                for p in range(len(article)):
                    if (article[p] == '_'):
                        article[p] = '[MASK]'
                        ops = tokenize_ops(data['options'][cnt], self.tokenizer)
                        if (p < 512):
                            first_sample.ph.append(p)
                            first_sample.ops.append(ops)
                            if data['answers']:
                                first_sample.ans.append(ord(data['answers'][cnt]) - ord('A'))
                        else:
                            shi += 1
                            second_sample.ph.append(p - second_s)
                            second_sample.ops.append(ops)
                            if data['answers']:
                                second_sample.ans.append(ord(data['answers'][cnt]) - ord('A'))
                        cnt += 1
                first_sample.article = article[:512]
                second_sample.article = article[-512:]
                if (shi == 0):#len(second_sample.ans)
                    return [first_sample]
                else:
                    return [first_sample, second_sample]


class Loader(object):
    def __init__(self, data_dir, data_file, cache_size, batch_size, device='cpu'):
        #self.tokenizer = BertTokenizer.from_pretrained(args.bert_model)
        self.data_dir = os.path.join(data_dir, data_file)
        print('loading {}'.format(self.data_dir))
        self.data = torch.load(self.data_dir)
        self.cache_size = cache_size
        self.batch_size = batch_size
        self.data_num = len(self.data)
        self.device = device
    
    def _batchify(self, data_set, data_batch):
            max_article_length = 0
            max_option_length = 0
            max_ops_num = 0
            bsz = len(data_batch)
            for idx in data_batch:
                data = data_set[idx]
                max_article_length = max(max_article_length, data.article.size(0))
                for ops in data.ops:
                    for op in ops:
                        max_option_length = max(max_option_length, op.size(0))
                max_ops_num  = max(max_ops_num, len(data.ops))
            articles = torch.zeros(bsz, max_article_length).long()
            articles_mask = torch.ones(articles.size())
            options = torch.zeros(bsz, max_ops_num, 4, max_option_length).long()
            options_mask = torch.ones(options.size())
            answers = torch.zeros(bsz, max_ops_num).long()
            mask = torch.zeros(answers.size())
            question_pos = torch.zeros(answers.size()).long()
            question_mask = torch.zeros(answers.size())
            file_name = []
            high_mask = torch.zeros(bsz) #indicate the sample belong to high school set
            for i, idx in enumerate(data_batch):
                data = data_set[idx]
                articles[i, :data.article.size(0)] = data.article
                articles_mask[i, data.article.size(0):] = 0
                for q, ops in enumerate(data.ops):
                    for k, op in enumerate(ops):
                        options[i,q,k,:op.size(0)] = op
                        options_mask[i,q,k, op.size(0):] = 0
                for q, ans in enumerate(data.ans):
                    answers[i,q] = ans
                    mask[i,q] = 1
                for q, pos in enumerate(data.ph):
                    question_pos[i,q] = pos
                    question_mask[i,q] = 1
                file_name.append(data.file_name)
            inp = [articles, articles_mask, options, options_mask, question_pos, mask, high_mask, question_mask]
            tgt = answers
            return inp, tgt, file_name
                
    def data_iter(self, shuffle=True):
        if (shuffle == True):
            random.shuffle(self.data)
        seqlen = torch.zeros(self.data_num)
        for i in range(self.data_num):
            seqlen[i] = self.data[i].article.size(0)
        cache_start = 0
        while (cache_start < self.data_num):
            cache_end = min(cache_start + self.cache_size, self.data_num)
            cache_data = self.data[cache_start:cache_end]
            seql = seqlen[cache_start:cache_end]
            _, indices = torch.sort(seql, descending=True)
            batch_start = 0
            while (batch_start + cache_start < cache_end):
                batch_end = min(batch_start + self.batch_size, cache_end - cache_start)
                data_batch = indices[batch_start:batch_end]
                inp, tgt, file_name = self._batchify(cache_data, data_batch)
                inp = to_device(inp, self.device)
                tgt = to_device(tgt, self.device)
                yield inp, tgt, file_name
                batch_start += self.batch_size
            cache_start += self.cache_size

                
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='albert cloth')
    args = parser.parse_args()
    data_collections = ['train', 'dev','test']
    for item in data_collections:    
        args.data_dir = './task3/ELE/{}'.format(item)
        args.pre = args.post = 0
        #args.path='./model/albert-base-v1/albert-base-spiece.model'
        args.bert_model = 'albert-xxlarge-v2'
        #args.save_name = './data/{}-{}.pt'.format(item, args.bert_model)
        args.save_name = './data/{}-{}.pt'.format(item, args.bert_model)
        diata = Preprocessor(args)