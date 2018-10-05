# -*- coding: utf-8 -*-
"""
Created on Mon Sep 10 12:14:19 2018

@author: quantummole
"""
import torch
import torch.nn as nn
import torch.nn.functional as tfunc
from torchvision import models
from model_blocks import RecConvLayer, ResidualBlock, InceptionLayer
#mode = -1 is for debug
#mode = 0 is for test and validation
#mode = {1,2,3..} is for training

class create_net :
    def __init__(self,net) :
        self.net = net
    def __call__(self,network_params,weights = None) :
        network = nn.DataParallel(self.net(**network_params))
        if weights :
            network.load_state_dict(torch.load(weights,map_location=lambda storage, loc: storage))
        return network

class DensenetModels(nn.Module) :
    def __init__(self,model) :
        super(DensenetModels,self).__init__()
        self.model = model(pretrained=True)
        self.final_layer_features = self.model.classifier.in_features
    def update_final_layer(self,final_layer) :
        self.model.classifier = final_layer
    def forward(self,inputs,mode) :
        outputs = []
        for inp in inputs :
            if len(inp.shape) == 3 :
                bs,m,n = inp.shape
                inp = torch.stack([inp,inp,inp],dim=1)
            outputs.append(self.model(inp))
        return outputs


class ResnetModels(nn.Module) :
    def __init__(self,model) :
        super(ResnetModels,self).__init__()
        self.model = model(pretrained=True)
        self.final_layer_features = self.model.fc.in_features
    def update_final_layer(self,final_layer) :
        self.model.fc = final_layer
    def forward(self,inputs,mode) :
        outputs = []
        for inp in inputs :
            if len(inp.shape) == 3 :
                bs,m,n = inp.shape
                inp = torch.stack([inp,inp,inp],dim=1)
            outputs.append(self.model(inp))
        return outputs

class PreTrainedClassifier(nn.Module) :
    def __init__(self,model_class,model,num_classes) :
        super(PreTrainedClassifier,self).__init__()
        self.model = model_class(model)
        self.num_classes = num_classes
        layer = nn.Sequential(nn.Linear(self.model.final_layer_features,2*self.model.final_layer_features),
                              nn.ReLU(),
                              nn.Dropout(),
                              nn.Linear(2*self.model.final_layer_features,self.num_classes))
        self.model.update_final_layer(layer)
    def forward(self,inputs,mode) :
        return self.model(inputs,mode)

class CustomNetClassification(nn.Module):
    def __init__(self,input_dim, final_conv_dim, initial_channels,growth_factor,num_classes,conv_module) :
        super(CustomNetClassification,self).__init__()
        self.layer = nn.ModuleList()
        while input_dim >= final_conv_dim :
            self.layer.append(nn.Sequential(conv_module(initial_channels,initial_channels+growth_factor),
                                            nn.MaxPool2d(kernel_size=3,stride=2,padding=1)))
            input_dim = input_dim//2
            initial_channels += growth_factor
        num_units = input_dim*input_dim*initial_channels
        self.output_layer = nn.Sequential(nn.Linear(num_units,num_classes))
        for m in self.modules():
                if isinstance(m, nn.Conv2d):
                    nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                elif isinstance(m, nn.BatchNorm2d):
                    nn.init.constant_(m.weight, 1)
                    nn.init.constant_(m.bias, 0)
                    
    def forward(self,inputs,mode) :
        outputs = []
        for inp in inputs :
            if len(inp.shape) == 3 :
                bs,m,n = inp.shape
                inp = inp.view(bs,1,m,n)
            bs,c,m,n = inp.shape
            for layer in self.layer :
                inp = layer(inp)
            inp = inp.view(bs,-1)    
            output = self.output_layer(inp)
            outputs.append(output)
        return outputs

class CustomNetSegmentation(nn.Module):
    class Encoder(nn.Module) :
        def __init__(self,depth,initial_channels,growth_factor) :
            super(CustomNetSegmentation.Encoder,self).__init__()
            self.layer = nn.ModuleList()
            self.expanders = nn.ModuleList()
            for i in range(depth) :
                self.layer.append(nn.Sequential(
                        ResidualBlock(initial_channels)))
                self.expanders.append(nn.Sequential(nn.Conv2d(initial_channels,initial_channels+growth_factor,stride=2,kernel_size=3,padding=1),
                        nn.PReLU(),
                        nn.GroupNorm(1,initial_channels+growth_factor),
                        ))
                initial_channels  = initial_channels + growth_factor
            self.final_channels = initial_channels
        def forward(self,x) :
            intermediate_outputs = []
            for i,layer in enumerate(self.layer) :
                x = layer(x)
                intermediate_outputs.append(x)
                x = self.expanders[i](x)
            return x,intermediate_outputs

    class Decoder(nn.Module) :
        def __init__(self,depth,initial_channels,growth_factor) :
            super(CustomNetSegmentation.Decoder,self).__init__()
            self.layer = nn.ModuleList()
            self.compressors = nn.ModuleList()
            for i in range(depth) :
                self.layer.append(nn.Sequential(
                        ResidualBlock(initial_channels)))
                self.compressors.append(nn.Sequential(
                        nn.Conv2d(initial_channels,initial_channels-growth_factor,3,padding=1),
                        nn.PReLU(),
                        nn.GroupNorm(1,initial_channels-growth_factor),
                        ))
                initial_channels  = initial_channels - growth_factor
            self.final_layer = ResidualBlock(initial_channels)
            self.final_channels = initial_channels
        def forward(self,x,intermediate_outputs) :
            num_intermediates = len(intermediate_outputs)
            for i,layer in enumerate(self.layer) :
                x = layer(x)
                upsample_layer = intermediate_outputs[num_intermediates -1 -i]
                bs,c,m,n = upsample_layer.shape
                x = tfunc.upsample(x,size=(m,n),mode='bilinear',align_corners=True)
                x = self.compressors[i](x)
                x = x + upsample_layer
            return self.final_layer(x)


            
    def __init__(self, depth, initial_channels,growth_factor,num_classes) :
        super(CustomNetSegmentation,self).__init__()
        self.inp_transformer = nn.Sequential(InceptionLayer(initial_channels,growth_factor))
        initial_channels = growth_factor
        self.encoder = CustomNetSegmentation.Encoder(depth,initial_channels,growth_factor)        
        self.decoder = CustomNetSegmentation.Decoder(depth,self.encoder.final_channels,growth_factor)
        self.output_layer = nn.Conv2d(self.decoder.final_channels,num_classes,1)
        for m in self.modules():
                if isinstance(m, nn.Conv2d):
                    nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                elif isinstance(m, nn.BatchNorm2d):
                    nn.init.constant_(m.weight, 1)
                    nn.init.constant_(m.bias, 0)
                    
    def forward(self,inputs,mode) :
        outputs = []
        for inp in inputs :
            if len(inp.shape) == 3 :
                bs,m,n = inp.shape
                inp = inp.view(bs,1,m,n)
            bs,c,m,n = inp.shape
            inp = self.inp_transformer(inp)
            x,intermediates = self.encoder(inp)
            x = self.decoder(x,intermediates)
            output = self.output_layer(inp+x)
            outputs.append(output)
        return outputs